"""Verbundene Geräte: je Gerät eine WebSocket-Verbindung (vom Gerät aufgebaut). Über sie laufen Anfragen (req/res),
Shells (pty_*) und die Messwerte. Zuschauer (Browser) bekommen Messwerte live; solange jemand zuschaut, schickt das Gerät öfter.
"""
import asyncio
import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass, field

from fastapi import WebSocket

log = logging.getLogger("health.hub")


class DeviceOffline(Exception):
    pass


class AgentError(Exception):
    pass


@dataclass
class Conn:
    dev: str
    ws: WebSocket
    since: float = field(default_factory=time.time)
    pending: dict = field(default_factory=dict)       # req-id -> Future
    ptys: dict = field(default_factory=dict)          # sid -> asyncio.Queue (Ausgaben fürs Browser-Terminal)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    interval: int = 0

    async def send(self, msg: dict) -> None:
        async with self.send_lock:
            await self.ws.send_text(json.dumps(msg, separators=(",", ":")))


class Hub:
    def __init__(self, live_interval: int = 2, idle_interval: int = 10):
        self.conns: dict[str, Conn] = {}
        self.viewers: dict[int, tuple[asyncio.Queue, str | None]] = {}  # id -> (Queue, Gerät im Blick oder None = Übersicht)
        self.live_interval, self.idle_interval = live_interval, idle_interval
        # Adresse von Health im Cloudflare Mesh, geht an alle Geräte. None = noch unbekannt (gleich nach dem Start): dann wird das Feld
        # gar nicht geschickt und die Geräte behalten ihren Wert. "" = Health ist sicher nicht im Mesh.
        self.mesh_server: str | None = None

    def online(self, dev: str) -> bool:
        return dev in self.conns

    # ---------- Verbindungen der Geräte ----------
    async def attach(self, c: Conn) -> None:
        old = self.conns.get(c.dev)
        self.conns[c.dev] = c
        if old:
            self._fail(old, "Gerät hat sich neu verbunden")
            try:
                await old.ws.close(code=4000)
            except Exception:  # noqa: BLE001
                pass
        await self.tune(c.dev)
        if self.mesh_server is not None:
            try:
                await c.send({"t": "config", "mesh_server": self.mesh_server})
            except Exception:  # noqa: BLE001
                pass

    async def set_mesh_server(self, url: str) -> None:
        """Neue Mesh-Adresse von Health an alle verbundenen Geräte schicken."""
        if url == self.mesh_server:
            return
        self.mesh_server = url
        for c in list(self.conns.values()):
            try:
                await c.send({"t": "config", "mesh_server": url})
            except Exception:  # noqa: BLE001
                pass

    def detach(self, c: Conn) -> None:
        if self.conns.get(c.dev) is c:
            del self.conns[c.dev]
        self._fail(c, "Gerät ist offline gegangen")

    def _fail(self, c: Conn, why: str) -> None:
        for f in c.pending.values():
            if not f.done():
                f.set_exception(DeviceOffline(why))
        c.pending.clear()
        for q in c.ptys.values():
            q.put_nowait({"t": "exit", "code": -1, "error": why})
        c.ptys.clear()

    def on_message(self, c: Conn, m: dict) -> None:
        t = m.get("t")
        if t == "res":
            f = c.pending.pop(str(m.get("id")), None)
            if f and not f.done():
                if m.get("ok"):
                    f.set_result(m.get("data") or {})
                else:
                    f.set_exception(AgentError(str(m.get("error") or "Fehler auf dem Gerät")))
        elif t == "pty_out":
            q = c.ptys.get(str(m.get("sid")))
            if q:
                q.put_nowait({"t": "out", "d": m.get("d") or ""})
        elif t == "pty_exit":
            q = c.ptys.pop(str(m.get("sid")), None)
            if q:
                q.put_nowait({"t": "exit", "code": m.get("code"), "error": m.get("error")})

    # ---------- Anfragen ----------
    async def request(self, dev: str, op: str, wait: float = 60, **kw) -> dict:
        c = self.conns.get(dev)
        if not c:
            raise DeviceOffline("Gerät ist gerade nicht verbunden")
        rid = secrets.token_hex(6)
        f = asyncio.get_running_loop().create_future()
        c.pending[rid] = f
        try:
            await c.send({"t": "req", "id": rid, "op": op, **kw})
            return await asyncio.wait_for(f, wait)
        except asyncio.TimeoutError as e:
            raise AgentError(f"Keine Antwort vom Gerät nach {int(wait)} s") from e
        finally:
            c.pending.pop(rid, None)

    async def exec(self, dev: str, cmd: str, timeout: int = 60, user: str = "") -> dict:
        timeout = max(1, min(int(timeout or 60), 3600))
        return await self.request(dev, "exec", wait=timeout + 15, cmd=cmd, user=user, timeout=timeout)

    # ---------- Shell ----------
    async def pty_open(self, dev: str, cols: int, rows: int) -> tuple[str, asyncio.Queue]:
        c = self.conns.get(dev)
        if not c:
            raise DeviceOffline("Gerät ist gerade nicht verbunden")
        sid = secrets.token_hex(6)
        q: asyncio.Queue = asyncio.Queue(maxsize=4000)
        c.ptys[sid] = q
        await c.send({"t": "pty_open", "sid": sid, "cols": cols, "rows": rows})
        return sid, q

    async def pty_send(self, dev: str, msg: dict) -> None:
        c = self.conns.get(dev)
        if c:
            await c.send(msg)

    async def pty_input(self, dev: str, sid: str, data: bytes) -> None:
        await self.pty_send(dev, {"t": "pty_in", "sid": sid, "d": base64.b64encode(data).decode()})

    async def pty_close(self, dev: str, sid: str) -> None:
        c = self.conns.get(dev)
        if c:
            c.ptys.pop(sid, None)
            try:
                await c.send({"t": "pty_close", "sid": sid})
            except Exception:  # noqa: BLE001
                pass

    # ---------- Zuschauer ----------
    def watch(self, focus: str | None) -> tuple[int, asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        vid = id(q)
        self.viewers[vid] = (q, focus)
        return vid, q

    def refocus(self, vid: int, focus: str | None) -> None:
        if vid in self.viewers:
            self.viewers[vid] = (self.viewers[vid][0], focus)

    def unwatch(self, vid: int) -> None:
        self.viewers.pop(vid, None)

    def publish(self, dev: str, msg: dict) -> None:
        for q, focus in list(self.viewers.values()):
            if q.full():
                continue
            if focus in (None, dev):
                q.put_nowait(msg)

    async def tune(self, dev: str | None = None) -> None:
        """Messabstand anpassen: live, solange jemand zuschaut (Übersicht oder dieses Gerät)."""
        watched_all = any(f is None for _, f in self.viewers.values())
        focused = {f for _, f in self.viewers.values() if f}
        for d, c in list(self.conns.items()):
            if dev and d != dev:
                continue
            want = self.live_interval if (d in focused) else (5 if watched_all else self.idle_interval)
            if c.interval != want:
                c.interval = want
                try:
                    await c.send({"t": "config", "interval": want})
                except Exception:  # noqa: BLE001
                    pass
