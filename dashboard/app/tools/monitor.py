"""Tool „Leistung“: misst regelmäßig CPU, RAM, Netz und Platte des Servers und jedes Containers,
speichert den Verlauf (store.py) und liefert Diagramm-Daten für beliebige Zeiträume."""
import asyncio
import logging
import socket
import time

from fastapi import APIRouter, HTTPException, Request

from .. import hostinfo
from ..config import get_settings
from ..discovery import ctr_name, stack_of
from ..docker_api import Docker, DockerError
from ..store import CTR_COLS, Store
from . import Tool

log = logging.getLogger("dashboard.monitor")
router = APIRouter()


class Sampler:
    """Eine Messung = Differenz zur vorigen (Zähler der CPU, Bytes im Netz …) geteilt durch die Zeit dazwischen."""

    def __init__(self, docker: Docker, store: Store):
        s = get_settings()
        self.docker, self.store = docker, store
        self.proc = hostinfo.proc_dir(s.host_proc)
        self.net_path = hostinfo.net_file(self.proc)
        self.only = [x.strip() for x in s.net_ifaces.split(",") if x.strip()]
        self.prev_host: tuple | None = None
        self.prev_ctr: dict[str, tuple] = {}
        self.now: dict = {"ts": None, "host": {}, "containers": []}
        self.host_name = socket.gethostname()
        self.ifaces: list[str] = []

    def host(self, t: float) -> dict:
        busy, total, cores = hostinfo.read_cpu(self.proc)
        rx, tx, self.ifaces = hostinfo.read_net(self.net_path, self.only)
        out = {**hostinfo.read_mem(self.proc), **hostinfo.read_disk(get_settings().host_root), "load1": hostinfo.read_load(self.proc),
               "cores": cores, "uptime": hostinfo.read_uptime(self.proc), "cpu": None, "net_rx": None, "net_tx": None}
        if self.prev_host:
            pb, pt, prx, ptx, pts = self.prev_host
            dt = max(t - pts, 0.001)
            out["cpu"] = round(max(0.0, (busy - pb) / max(total - pt, 1) * 100), 2)
            out["net_rx"] = max(0.0, (rx - prx) / dt)
            out["net_tx"] = max(0.0, (tx - ptx) / dt)
        self.prev_host = (busy, total, rx, tx, t)
        return out

    async def containers(self, t: float) -> list[dict]:
        try:
            cs = [c for c in await self.docker.containers() if c.get("State") == "running"]
        except DockerError as e:
            log.warning("Container-Liste: %s", e)
            return []
        stats = await self.docker.gather([self.docker.stats(c["Id"]) for c in cs])
        out, seen = [], set()
        for c, st in zip(cs, stats):
            if not st:
                continue
            cid = c["Id"]
            seen.add(cid)
            cpu = st.get("cpu_stats") or {}
            ct, sysu = (cpu.get("cpu_usage") or {}).get("total_usage", 0), cpu.get("system_cpu_usage", 0)
            mem = st.get("memory_stats") or {}
            ms = mem.get("stats") or {}
            used = max(0, mem.get("usage", 0) - ms.get("inactive_file", ms.get("total_inactive_file", 0)))
            nets = (st.get("networks") or {}).values()
            rx, tx = sum(n.get("rx_bytes", 0) for n in nets), sum(n.get("tx_bytes", 0) for n in nets)
            br = bw = 0
            for e in ((st.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []):
                op = (e.get("op") or "").lower()
                br += e.get("value", 0) if op == "read" else 0
                bw += e.get("value", 0) if op == "write" else 0
            row = {"id": cid[:12], "name": ctr_name(c), "stack": stack_of(c), "mem": used, "mem_limit": mem.get("limit"),
                   "cpu": None, "net_rx": None, "net_tx": None, "blk_r": None, "blk_w": None}
            p = self.prev_ctr.get(cid)
            if p:
                pct, psys, prx, ptx, pbr, pbw, pts = p
                dt = max(t - pts, 0.001)
                # Anteil am ganzen Server (alle Kerne = 100 %), passt so zur Server-CPU
                row["cpu"] = round(max(0.0, (ct - pct) / max(sysu - psys, 1) * 100), 3) if sysu > psys else 0.0
                row["net_rx"], row["net_tx"] = max(0.0, (rx - prx) / dt), max(0.0, (tx - ptx) / dt)
                row["blk_r"], row["blk_w"] = max(0.0, (br - pbr) / dt), max(0.0, (bw - pbw) / dt)
            self.prev_ctr[cid] = (ct, sysu, rx, tx, br, bw, t)
            out.append(row)
        for gone in set(self.prev_ctr) - seen:
            self.prev_ctr.pop(gone, None)
        return out

    async def tick(self) -> None:
        t = time.time()
        host = await asyncio.to_thread(self.host, t)
        ctrs = await self.containers(t)
        self.now = {"ts": int(t), "host": host, "containers": ctrs}
        if host["cpu"] is not None:  # die allererste Messung hat noch keine Differenz
            ts = int(t)
            await asyncio.to_thread(self.store.add, ts, host, [c for c in ctrs if c["cpu"] is not None])


async def loop(app):
    s = get_settings()
    sampler: Sampler = app.state.sampler
    last_compact = 0.0
    while True:
        started = time.monotonic()
        try:
            await sampler.tick()
            if time.time() - last_compact > 120:
                await asyncio.to_thread(app.state.store.compact)
                last_compact = time.time()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 – die Messung darf nie ganz stehen bleiben
            log.exception("Messung fehlgeschlagen")
        await asyncio.sleep(max(1.0, s.sample_seconds - (time.monotonic() - started)))


async def start(app):
    app.state.sampler = Sampler(app.state.docker, app.state.store)
    app.state.monitor_task = asyncio.create_task(loop(app))


async def stop(app):
    t = getattr(app.state, "monitor_task", None)
    if t:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


# ---------- API ----------
def span(start: int | None, end: int | None) -> tuple[int, int]:
    now = int(time.time())
    end = min(int(end or now), now + 60)
    start = int(start or end - 3600)
    if start >= end:
        raise HTTPException(400, "Zeitraum ungültig")
    return start, end


@router.get("/api/v1/monitor/now")
async def now(req: Request):
    sm: Sampler = req.app.state.sampler
    return {**sm.now, "hostname": sm.host_name, "ifaces": sm.ifaces, "sample": get_settings().sample_seconds,
            "oldest": await asyncio.to_thread(req.app.state.store.oldest)}


@router.get("/api/v1/monitor/host")
async def host_series(req: Request, start: int | None = None, end: int | None = None, points: int = 300):
    a, b = span(start, end)
    return await asyncio.to_thread(req.app.state.store.host_series, a, b, max(20, min(points, 1500)))


@router.get("/api/v1/monitor/usage")
async def usage(req: Request, start: int | None = None, end: int | None = None):
    a, b = span(start, end)
    return await asyncio.to_thread(req.app.state.store.ctr_usage, a, b)


@router.get("/api/v1/monitor/series")
async def series(req: Request, metric: str = "cpu", group: str = "stack", names: str = "", top: int = 6,
                 start: int | None = None, end: int | None = None, points: int = 200):
    if metric not in CTR_COLS:
        raise HTTPException(400, "Unbekannte Größe")
    a, b = span(start, end)
    st: Store = req.app.state.store
    picked = [n for n in names.split(",") if n]
    if not picked:
        # die größten Verbraucher im Zeitraum
        totals: dict[str, float] = {}
        for u in await asyncio.to_thread(st.ctr_usage, a, b):
            k = u["stack"] if group == "stack" else u["name"]
            totals[k] = totals.get(k, 0) + (u.get(metric) if metric in ("cpu", "mem") else u.get({"net_rx": "rx", "net_tx": "tx", "blk_r": "blk_r", "blk_w": "blk_w"}[metric])) or 0
        picked = [k for k, _ in sorted(totals.items(), key=lambda x: -x[1])[:max(1, min(top, 12))]]
    if not picked:
        return {"step": 0, "t": [], "series": {}}
    return await asyncio.to_thread(st.ctr_series, a, b, metric, picked, group, max(20, min(points, 1000)))


tool = Tool(id="monitor", name="Leistung", icon="monitoring", router=router, start=start, stop=stop)
