"""Schlanker Client für die Docker-Engine-API (über den Socket oder TCP, z. B. einen Socket-Proxy)."""
import asyncio
import json
import logging
import struct

import httpx

log = logging.getLogger("dashboard.docker")


class DockerError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def make_client(host: str) -> httpx.AsyncClient:
    timeout = httpx.Timeout(connect=5, read=120, write=30, pool=10)
    if host.startswith("unix://"):
        return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=host[len("unix://"):]), base_url="http://docker", timeout=timeout)
    return httpx.AsyncClient(base_url=host.replace("tcp://", "http://", 1).rstrip("/"), timeout=timeout)


class Docker:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = await self.client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise DockerError(f"Docker nicht erreichbar: {e}") from e
        if r.status_code >= 400:
            try:
                msg = r.json().get("message") or r.text
            except ValueError:
                msg = r.text
            raise DockerError(msg or f"Docker-Fehler {r.status_code}", 404 if r.status_code == 404 else 400 if r.status_code < 500 else 502)
        return r

    async def get(self, path: str, **params):
        return (await self._req("GET", path, params=params or None)).json()

    async def post(self, path: str, **params):
        r = await self._req("POST", path, params=params or None)
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None

    # ---- Abfragen ----
    async def info(self) -> dict:
        return await self.get("/info")

    async def containers(self) -> list[dict]:
        return await self.get("/containers/json", all="true")

    async def inspect(self, cid: str) -> dict:
        return await self.get(f"/containers/{cid}/json")

    async def stats(self, cid: str) -> dict | None:
        try:
            return await self.get(f"/containers/{cid}/stats", stream="false", **{"one-shot": "true"})
        except DockerError:
            return None

    async def df(self) -> dict:
        return await self.get("/system/df")

    async def logs(self, cid: str, tail: int = 300, tty: bool = False) -> str:
        r = await self._req("GET", f"/containers/{cid}/logs", params={"stdout": "1", "stderr": "1", "tail": str(tail), "timestamps": "1"})
        return r.text if tty else demux(r.content)

    # ---- Aktionen ----
    async def action(self, cid: str, what: str) -> None:
        if what not in ("start", "stop", "restart"):
            raise DockerError("Unbekannte Aktion", 400)
        await self._req("POST", f"/containers/{cid}/{what}", params={"t": "20"} if what != "start" else None)

    async def prune_images(self) -> dict:
        return await self.post("/images/prune", filters=json.dumps({"dangling": ["false"]})) or {}

    async def prune_build_cache(self) -> dict:
        return await self.post("/build/prune") or {}

    async def gather(self, coros, limit: int = 8):
        sem = asyncio.Semaphore(limit)

        async def one(c):
            async with sem:
                return await c
        return await asyncio.gather(*(one(c) for c in coros))


def demux(raw: bytes) -> str:
    """Docker-Logs ohne TTY kommen in Rahmen (8 Byte Kopf: Stream, 0, 0, 0, Länge)."""
    out, i = [], 0
    while i + 8 <= len(raw):
        if raw[i] not in (0, 1, 2) or raw[i + 1:i + 4] != b"\0\0\0":
            return raw.decode("utf-8", "replace")  # doch kein Rahmenformat
        n = struct.unpack(">I", raw[i + 4:i + 8])[0]
        out.append(raw[i + 8:i + 8 + n])
        i += 8 + n
    return b"".join(out).decode("utf-8", "replace")
