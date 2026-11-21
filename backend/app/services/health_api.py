"""IDA Health intern (HEALTH_URL + HEALTH_IDA_TOKEN, siehe docs/HEALTH.md): Geräte mit ida-connect – Status, Befehle, Dateien,
Docker und Updates. Die geraet_*-Tools (services/mcp/device_tools.py) gehen hier durch. Leer = aus.
"""
import time

import httpx

from ..config import get_settings
from .errors import Invalid, NotFound

# „Mit IDA verbinden“ in Health trägt Adresse + Token hier ein (Einstellung health_link, PUT /api/v1/health-link) –
# das gilt vor HEALTH_URL / HEALTH_IDA_TOKEN aus der .env.
KEY = "health_link"
_link: dict = {"url": "", "token": "", "at": 0.0}


async def load(force: bool = False) -> None:
    """Verbindung aus der Datenbank lesen (höchstens alle 30 s)."""
    if not force and time.monotonic() - _link["at"] < 30:
        return
    from ..db import session_scope
    from . import settings_store
    try:
        async with session_scope() as db:
            v = await settings_store.get(db, KEY) or {}
    except Exception:  # noqa: BLE001 – ohne Datenbank gilt die .env
        v = {}
    _link.update(url=str(v.get("url") or ""), token=str(v.get("token") or ""), at=time.monotonic())


def url() -> str:
    return (_link["url"] or get_settings().health_url).strip()


def token() -> str:
    return _link["token"] or get_settings().health_ida_token


def enabled() -> bool:
    return bool(url())


async def _req(method: str, path: str, *, timeout: float = 30, **kw) -> dict:
    await load()
    if not enabled():
        raise Invalid("IDA Health ist nicht eingerichtet (in Health: Einstellungen → „Mit IDA verbinden“)")
    tok = token()
    headers = {"authorization": f"Bearer {tok}"} if tok else {}
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
            r = await c.request(method, url().rstrip("/") + "/api/v1" + path, **kw)
    except httpx.HTTPError as e:
        raise Invalid(f"IDA Health nicht erreichbar: {e}") from e
    try:
        data = r.json()
    except ValueError:
        data = {"detail": r.text[:300]}
    if r.status_code == 404:
        raise NotFound(str(data.get("detail") or "Nicht gefunden"))
    if r.status_code >= 400:
        raise Invalid(str(data.get("detail") or f"Health antwortet {r.status_code}"))
    return data


def summary(d: dict) -> dict:
    """Kurzform eines Geräts (für Listen und den Prompt)."""
    m, i = d.get("latest") or {}, d.get("info") or {}
    mem = m.get("mem") or [None, None]
    disks = m.get("disks") or []
    temps = [t[1] for t in m.get("temps") or [] if len(t) > 1]
    gb = lambda b: None if b is None else round(b / 1024 ** 3, 1)  # noqa: E731
    return {"id": d["id"], "name": d["name"], "online": d.get("online"), "system": " · ".join(x for x in (i.get("os"), i.get("arch")) if x),
            "cpu_prozent": m.get("cpu"), "ram_gb": [gb(mem[0]), gb(mem[1])], "temperatur_c": max(temps) if temps else None,
            "platten": [{"mount": x[0], "belegt_gb": gb(x[1]), "gesamt_gb": gb(x[2])} for x in disks],
            "mesh_ip": (i.get("mesh") or {}).get("ip") or None, "notizen": d.get("notes") or None}


async def devices() -> list[dict]:
    return (await _req("GET", "/devices"))["devices"]


async def device(key: str) -> dict:
    return await _req("GET", f"/devices/{_k(key)}")


def _k(key: str) -> str:
    key = (key or "").strip()
    if not key:
        raise Invalid("Gerät fehlt (id oder Name aus geraete_liste)")
    from urllib.parse import quote
    return quote(key, safe="")


async def exec_cmd(key: str, cmd: str, timeout: int = 60, user: str = "") -> dict:
    timeout = max(1, min(int(timeout or 60), 3600))
    return await _req("POST", f"/devices/{_k(key)}/exec", json={"cmd": cmd, "timeout": timeout, "user": user}, timeout=timeout + 30)


async def fs_list(key: str, path: str) -> dict:
    return await _req("GET", f"/devices/{_k(key)}/fs/list", params={"path": path or "/"})


async def fs_read(key: str, path: str) -> dict:
    return await _req("GET", f"/devices/{_k(key)}/fs/read", params={"path": path}, timeout=90)


async def fs_write(key: str, path: str, content: str) -> dict:
    return await _req("POST", f"/devices/{_k(key)}/fs/write", json={"path": path, "content": content}, timeout=150)


async def docker(key: str) -> dict:
    return await _req("GET", f"/devices/{_k(key)}/docker", timeout=60)


async def stack_action(key: str, name: str, action: str) -> dict:
    return await _req("POST", f"/devices/{_k(key)}/docker/stack", json={"name": name, "action": action}, timeout=960)


async def container_action(key: str, name: str, action: str) -> dict:
    return await _req("POST", f"/devices/{_k(key)}/docker/container", json={"name": name, "action": action}, timeout=150)


async def container_logs(key: str, name: str, tail: int = 200) -> dict:
    return await _req("GET", f"/devices/{_k(key)}/docker/logs", params={"name": name, "tail": tail}, timeout=60)


async def update(key: str, target: str) -> dict:
    return await _req("POST", f"/devices/{_k(key)}/update", json={"target": target}, timeout=3100)


async def history(key: str, seconds: int = 3600) -> dict:
    return await _req("GET", f"/devices/{_k(key)}/history", params={"range": seconds, "points": 60})
