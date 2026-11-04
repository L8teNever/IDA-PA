"""Sandbox-Dienst: je Code-Thread ein eigener Docker-Container, in dem die KI und der Nutzer Befehle ausführen.

Nur dieser Dienst bekommt den Docker-Socket, und er kann nichts außer Sandbox-Containern anlegen: festes Image, keine Ordner
vom Server, eigenes Netz, RAM-Grenze. Er kennt keine Zugangsdaten von IDA. Läuft 15 Minuten kein Befehl, wird die Sandbox
gestoppt (auch was darin noch läuft); ihr Ordner bleibt, nach 7 Tagen ohne Nutzung wird alles gelöscht.
Siehe docs/CODE.md. Anfragen brauchen x-sandbox-secret.
"""
import asyncio
import hmac
import logging
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

log = logging.getLogger("ida.sandbox")
IMAGE, TAG = os.environ.get("SANDBOX_IMAGE", "node:22-bookworm").rsplit(":", 1)  # bringt Node, Python, git und Compiler mit
NETWORK = "ida-sandbox"
GB = 1024 ** 3
BASE_GB = 2
MAX_GB = int(os.environ.get("SANDBOX_MAX_GB", "6"))
CPUS = 2
IDLE_S = int(os.environ.get("SANDBOX_IDLE_S", str(15 * 60)))
KEEP_S = 7 * 24 * 3600
MAX_TIMEOUT = 600
OUTPUT = 20000  # so viele Zeichen vom Ende der Ausgabe kommen zurück


def safe_thread(thread: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", thread or ""):
        raise HTTPException(400, "Ungültiger Thread")
    return thread


def name_of(thread: str) -> str:
    return f"ida-sandbox-{safe_thread(thread)}"


def demux(raw: bytes) -> str:
    """Docker schickt stdout und stderr in Rahmen: 8 Byte Kopf (Typ, 0, 0, 0, Länge) und dann die Daten."""
    out, i = bytearray(), 0
    while i + 8 <= len(raw):
        size = int.from_bytes(raw[i + 4:i + 8], "big")
        out += raw[i + 8:i + 8 + size]
        i += 8 + size
    return out.decode("utf-8", "replace")


class Sandboxes:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.docker = httpx.AsyncClient(transport=transport or httpx.AsyncHTTPTransport(uds="/var/run/docker.sock"),
                                        base_url="http://docker", timeout=httpx.Timeout(60, read=None))
        self.last: dict[str, float] = {}    # Thread -> letzte Aktivität
        self.active: dict[str, int] = {}    # Thread -> laufende Befehle

    async def _inspect(self, name: str) -> dict | None:
        r = await self.docker.get(f"/containers/{name}/json")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def _prepare(self) -> None:
        r = await self.docker.get(f"/images/{IMAGE}:{TAG}/json")
        if r.status_code == 404:
            async with self.docker.stream("POST", "/images/create", params={"fromImage": IMAGE, "tag": TAG}) as s:
                s.raise_for_status()
                async for _ in s.aiter_raw():
                    pass
        r = await self.docker.post("/networks/create", json={"Name": NETWORK, "CheckDuplicate": True,
                                                             "Options": {"com.docker.network.bridge.enable_icc": "false",
                                                                         "com.docker.network.bridge.name": "ida-sandbox0"}})  # an diesem Namen hängt die Firewall
        if r.status_code not in (201, 409):  # 409: gibt es schon
            r.raise_for_status()

    async def ensure(self, thread: str) -> str:
        """Läuft die Sandbox? Sonst anlegen oder wieder starten. Ein Neustart beginnt immer mit dem Grund-RAM."""
        name = name_of(thread)
        info = await self._inspect(name)
        if info and info["State"]["Running"]:
            return name
        if not info:
            await self._prepare()
            limit = BASE_GB * GB
            r = await self.docker.post("/containers/create", params={"name": name}, json={
                "Image": f"{IMAGE}:{TAG}", "Cmd": ["sleep", "infinity"], "WorkingDir": "/workspace", "Env": ["CI=1"],
                "Labels": {"ida.sandbox": "1", "ida.sandbox.thread": thread},
                "HostConfig": {"Memory": limit, "MemorySwap": limit, "NanoCpus": CPUS * 10 ** 9, "PidsLimit": 1024,
                               "Binds": [f"{name}:/workspace"], "NetworkMode": NETWORK,
                               "SecurityOpt": ["no-new-privileges:true"], "CapDrop": ["NET_RAW", "MKNOD", "AUDIT_WRITE"]}})
            r.raise_for_status()
        else:
            await self._set_memory(name, BASE_GB)
        r = await self.docker.post(f"/containers/{name}/start")
        if r.status_code not in (204, 304):
            r.raise_for_status()
        self.last[thread] = time.time()
        return name

    async def _set_memory(self, name: str, gb: int) -> None:
        r = await self.docker.post(f"/containers/{name}/update", json={"Memory": gb * GB, "MemorySwap": gb * GB})
        r.raise_for_status()

    async def run(self, thread: str, command: str, timeout: int) -> dict:
        timeout = max(1, min(int(timeout or 120), MAX_TIMEOUT))
        self.active[thread] = self.active.get(thread, 0) + 1
        self.last[thread] = time.time()
        try:
            name = await self.ensure(thread)
            r = await self.docker.post(f"/containers/{name}/exec", json={
                "AttachStdout": True, "AttachStderr": True, "Tty": False, "WorkingDir": "/workspace",
                "Cmd": ["timeout", "-k", "5", str(timeout), "bash", "-lc", command]})
            r.raise_for_status()
            exec_id = r.json()["Id"]
            raw = bytearray()
            async with self.docker.stream("POST", f"/exec/{exec_id}/start", json={"Detach": False, "Tty": False}) as s:
                s.raise_for_status()
                async for chunk in s.aiter_raw():
                    if len(raw) < 8 * 1024 * 1024:  # sehr viel Ausgabe: weiterlesen, aber nicht alles behalten
                        raw += chunk
            code = (await self.docker.get(f"/exec/{exec_id}/json")).json().get("ExitCode")
            text = demux(bytes(raw))
            out = {"exit": code, "ausgabe": text[-OUTPUT:], "gekuerzt": len(text) > OUTPUT}
            if code == 124:
                out["hinweis"] = f"Nach {timeout} s abgebrochen."
            elif code == 137:
                out["hinweis"] = "Der Befehl wurde hart beendet, vermutlich reicht der RAM nicht."
            return out
        finally:
            self.active[thread] -= 1
            self.last[thread] = time.time()

    async def stop(self, thread: str) -> None:
        r = await self.docker.post(f"/containers/{name_of(thread)}/stop", params={"t": 1})
        if r.status_code not in (204, 304, 404):
            r.raise_for_status()

    async def status(self, thread: str) -> dict:
        info = await self._inspect(name_of(thread))
        if not info:
            return {"vorhanden": False, "laeuft": False, "ram_gb": BASE_GB, "max_gb": MAX_GB}
        idle = max(0, int(IDLE_S - (time.time() - self.last.get(thread, 0))))
        return {"vorhanden": True, "laeuft": info["State"]["Running"], "ram_gb": round(info["HostConfig"]["Memory"] / GB, 1), "max_gb": MAX_GB,
                "stoppt_in_s": idle if info["State"]["Running"] else None}

    async def sweep(self, now: float | None = None) -> None:
        """Stoppt untätige Sandboxen und löscht alte. Nach einem Neustart dieses Dienstes zählt die Zeit von vorn."""
        now = now or time.time()
        r = await self.docker.get("/containers/json", params={"all": "true", "filters": '{"label":["ida.sandbox=1"]}'})
        r.raise_for_status()
        for c in r.json():
            thread = c["Labels"].get("ida.sandbox.thread", "")
            if not thread:
                continue
            if c["State"] == "running":
                self.last.setdefault(thread, now)
                if not self.active.get(thread) and now - self.last[thread] >= IDLE_S:
                    log.info("Sandbox %s: %d s ohne Aktivität, wird gestoppt", thread, IDLE_S)
                    await self.stop(thread)
            elif c["State"] == "exited":
                info = await self._inspect(name_of(thread))
                done = datetime.fromisoformat((info or {}).get("State", {}).get("FinishedAt", "")[:19]).replace(tzinfo=timezone.utc).timestamp() if info else now
                if now - done >= KEEP_S:
                    log.info("Sandbox %s: wird gelöscht", thread)
                    await self.docker.delete(f"/containers/{name_of(thread)}", params={"force": "true"})
                    await self.docker.delete(f"/volumes/{name_of(thread)}")
                    self.last.pop(thread, None)


def create_app(boxes: Sandboxes | None = None) -> FastAPI:
    box = boxes or Sandboxes()
    secret = os.environ.get("SANDBOX_SECRET", "")

    async def loop():
        while True:
            await asyncio.sleep(30)
            try:
                await box.sweep()
            except Exception:  # noqa: BLE001
                log.exception("Aufräumen fehlgeschlagen")

    @asynccontextmanager
    async def lifespan(_):
        task = asyncio.create_task(loop())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def check(given: str | None) -> None:
        if not secret or not given or not hmac.compare_digest(given, secret):
            raise HTTPException(403, "Kein Zugriff")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/sandbox/{thread}/run")
    async def run(thread: str, request: Request, x_sandbox_secret: str | None = Header(None)):
        check(x_sandbox_secret)
        b = await request.json()
        safe_thread(thread)
        if not str(b.get("befehl") or "").strip():
            raise HTTPException(400, "Befehl fehlt")
        return await box.run(thread, str(b["befehl"]), int(b.get("timeout_s") or 120))

    @app.put("/sandbox/{thread}/files")
    async def files(thread: str, request: Request, x_sandbox_secret: str | None = Header(None)):
        """Projektdateien als tar nach /workspace (Kopie: die Sandbox sieht nie den echten Projektordner)."""
        check(x_sandbox_secret)
        name = await box.ensure(thread)
        box.last[thread] = time.time()
        r = await box.docker.put(f"/containers/{name}/archive", params={"path": "/workspace"}, content=await request.body(),
                                 headers={"content-type": "application/x-tar"})
        r.raise_for_status()
        return {"ok": True}

    @app.post("/sandbox/{thread}/memory")
    async def memory(thread: str, request: Request, x_sandbox_secret: str | None = Header(None)):
        check(x_sandbox_secret)
        gb = int((await request.json()).get("gb") or 0)
        if not BASE_GB <= gb <= MAX_GB:
            raise HTTPException(400, f"Erlaubt sind {BASE_GB} bis {MAX_GB} GB")
        name = await box.ensure(thread)
        await box._set_memory(name, gb)
        return await box.status(thread)

    @app.post("/sandbox/{thread}/stop")
    async def stop(thread: str, x_sandbox_secret: str | None = Header(None)):
        check(x_sandbox_secret)
        await box.stop(thread)
        return await box.status(thread)

    @app.get("/sandbox/{thread}")
    async def status(thread: str, x_sandbox_secret: str | None = Header(None)):
        check(x_sandbox_secret)
        return await box.status(thread)

    return app


app = create_app()
