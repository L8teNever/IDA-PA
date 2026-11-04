"""Öffentliche Adressen der Container: welcher Hostname des Cloudflare-Tunnels zeigt auf welchen Container?
Docker weiß das nicht, cloudflared schon: sein Metrics-Port gibt unter /config die aktuelle Zuordnung heraus (ganz ohne
API-Token). Die Docker-Netze sind gegeneinander abgeschottet, deshalb tritt das Dashboard bei Bedarf dem Netz von cloudflared
bei. Klappt etwas davon nicht, fehlen nur die Adressen; Ports, Netze und Mounts zeigt das Dashboard trotzdem."""
import logging
import re
import time
from urllib.parse import urlsplit

import httpx

from .discovery import ctr_name, is_self, networks_of
from .docker_api import Docker, DockerError

log = logging.getLogger("dashboard.tunnels")

TTL = 60  # so lange (Sekunden) gilt die gelesene Zuordnung
DEFAULT_PORTS = (2000, 20241, 20242, 20243, 20244, 20245)  # 20241 ff. nimmt cloudflared selbst, wenn nichts eingestellt ist
LOCAL_ONLY = ("127.0.0.1", "::1")

_cache: tuple[float, list[dict]] = (0.0, [])


async def fetch_config(url: str) -> dict:
    async with httpx.AsyncClient(timeout=2) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


def metrics_ports(info: dict) -> list[int]:
    """Der eingestellte Port zuerst. Aus der Umgebung wird nur TUNNEL_METRICS gelesen: dort steht sonst auch der Tunnel-Token."""
    cfg = info.get("Config") or {}
    found = [e.split("=", 1)[1] for e in cfg.get("Env") or [] if e.startswith("TUNNEL_METRICS=")]
    args = cfg.get("Cmd") or []
    found += [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--metrics"]
    ports = [int(m.group(1)) for x in found if (m := re.search(r":(\d+)$", x))]
    return list(dict.fromkeys(ports + list(DEFAULT_PORTS)))


async def _reachable_ip(docker: Docker, tunnel: dict, me: dict | None) -> str | None:
    """Adresse von cloudflared in einem Netz, das wir mit ihm teilen; sonst treten wir seinem Netz bei."""
    nets = {n["name"]: n["ip"] for n in networks_of(tunnel)}
    mine = {n["name"] for n in networks_of(me)} if me else set()
    shared = sorted(set(nets) & mine)
    if shared:
        return nets[shared[0]]
    if not me or not nets:
        return None
    name = sorted(nets)[0]
    await docker.connect_network(name, me["Id"])
    log.info("Dashboard ist dem Netz %s beigetreten, um die Tunnel-Adressen zu lesen", name)
    return nets[name]


async def _read(docker: Docker, containers: list[dict]) -> list[dict]:
    me = next((c for c in containers if is_self(c)), None)
    out: list[dict] = []
    for t in containers:
        if "cloudflared" not in (t.get("Image") or "") or t.get("State") != "running":
            continue
        try:
            ip = await _reachable_ip(docker, t, me)
            ports = metrics_ports(await docker.inspect(t["Id"]))
        except DockerError as e:
            log.warning("Tunnel %s nicht lesbar: %s", ctr_name(t), e)
            continue
        for port in ports if ip else ():
            try:
                cfg = await fetch_config(f"http://{ip}:{port}/config")
            except (httpx.HTTPError, ValueError):
                continue
            out += [{"host": r["hostname"], "service": r.get("service") or "", "path": r.get("path")}
                    for r in (cfg.get("config") or {}).get("ingress") or [] if r.get("hostname")]
            break
    return out


async def routes(docker: Docker, containers: list[dict]) -> list[dict]:
    global _cache
    if time.monotonic() - _cache[0] < TTL:
        return _cache[1]
    found = await _read(docker, containers)
    _cache = (time.monotonic(), found)
    return found


def reset() -> None:
    global _cache
    _cache = (0.0, [])


def match(found: list[dict], containers: list[dict]) -> dict[str, list[dict]]:
    """Container-Id → seine öffentlichen Adressen. Das Ziel ist ein Container (Name, Dienst, IP) oder ein veröffentlichter Port."""
    by_name: dict[str, dict] = {}
    by_port: dict[int, dict] = {}
    for c in containers:
        keys = [ctr_name(c), (c.get("Labels") or {}).get("com.docker.compose.service"), *[n["ip"] for n in networks_of(c)]]
        by_name.update({k: c for k in keys if k})
        for p in c.get("Ports") or []:
            if p.get("PublicPort") and p.get("IP") not in LOCAL_ONLY:  # cloudflared läuft im Container und kommt da nicht hin
                by_port[p["PublicPort"]] = c
    out: dict[str, list[dict]] = {}
    for r in found:
        u = urlsplit(r["service"])
        if not u.hostname:
            continue  # http_status:404 und Ähnliches
        try:
            port = u.port or {"http": 80, "https": 443}.get(u.scheme)
        except ValueError:
            continue
        c = by_name.get(u.hostname) or by_port.get(port)
        if c:
            out.setdefault(c["Id"], []).append({"url": f"https://{r['host']}{r.get('path') or ''}", "target": r["service"]})
    return out
