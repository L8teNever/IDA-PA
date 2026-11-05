"""Tool „Ports“ (Dashboard, Nachfolger von Carlas Ports-Manager): alle offenen TCP/UDP-Ports des Servers mit Zuordnung.

Woher die Daten kommen:
* Offene Ports: /proc/net/{tcp,tcp6,udp,udp6} im Netz des Servers (HOST_PROC/1/net – ohne Shell-Befehle, ohne ss/netstat).
* Prozess je Port: über die Socket-Inodes in HOST_PROC/<pid>/fd (wenn der Container das lesen darf; sonst bleibt es leer).
* Docker: veröffentlichte Ports der Container (Stack, Dienst, Port im Container) – auch, wenn /proc nichts liefert.
* Tunnel: welche öffentliche Adresse auf IP:Port zeigt, samt Zero-Trust-Schutz (Cloudflare-API, wie beim Graph).

Ein Port ist eine Zeile (IPv4 und IPv6 zusammen); „alle Schnittstellen“ (0.0.0.0/::) wird markiert, weil der Port dann auch über
die öffentliche IP des Servers erreichbar ist, wenn keine Firewall davor sitzt.
"""
import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from .. import cloudflare_api, tunnels
from ..config import get_settings
from ..discovery import ctr_name, discover
from ..docker_api import DockerError
from ..hostinfo import proc_dir
from . import Tool

router = APIRouter()

TCP_LISTEN = "0A"
UDP_UNCONN = "07"
ANY = ("0.0.0.0", "::")


# ---------------- /proc/net lesen ----------------
def _ip(hexip: str) -> str:
    if len(hexip) == 8:
        return str(ipaddress.IPv4Address(bytes.fromhex(hexip)[::-1]))
    words = [bytes.fromhex(hexip[i:i + 8])[::-1] for i in range(0, 32, 8)]  # 4 Wörter, je little-endian
    a = ipaddress.IPv6Address(b"".join(words))
    return str(a.ipv4_mapped) if a.ipv4_mapped else str(a)


def parse_net(text: str, proto: str) -> list[dict]:
    """Zeilen aus /proc/net/tcp|udp(6): nur lauschende Sockets."""
    out = []
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) < 10:
            continue
        local, state, inode = f[1], f[3], f[9]
        if state != (TCP_LISTEN if proto == "tcp" else UDP_UNCONN):
            continue
        hexip, _, hexport = local.partition(":")
        try:
            out.append({"ip": _ip(hexip), "port": int(hexport, 16), "proto": proto, "inode": inode})
        except ValueError:
            continue
    return out


def host_sockets(proc: Path) -> list[dict]:
    net = proc / "1" / "net"
    if not net.is_dir():
        net = proc / "net"
    found = []
    for name, proto in (("tcp", "tcp"), ("tcp6", "tcp"), ("udp", "udp"), ("udp6", "udp")):
        try:
            found += parse_net((net / name).read_text(), proto)
        except OSError:
            continue
    return found


def processes(proc: Path, inodes: set[str]) -> dict[str, dict]:
    """Socket-Inode → {pid, name}. Best effort: ohne Rechte auf fremde fd-Ordner bleibt es leer."""
    out: dict[str, dict] = {}
    if not inodes:
        return out
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        fd = proc / pid / "fd"
        try:
            links = os.listdir(fd)
        except OSError:
            continue
        for l in links:
            try:
                t = os.readlink(fd / l)
            except OSError:
                continue
            if t.startswith("socket:[") and (ino := t[8:-1]) in inodes and ino not in out:
                try:
                    name = (proc / pid / "comm").read_text().strip()
                except OSError:
                    name = ""
                out[ino] = {"pid": int(pid), "name": name}
        if len(out) == len(inodes):
            break
    return out


def scope(ip: str) -> str:
    """all = alle Schnittstellen, local = nur der Server selbst, private = internes Netz, public = öffentliche IP."""
    if ip in ANY or ip == "*":
        return "all"
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "private"
    if a.is_loopback:
        return "local"
    if a.is_private or a.is_link_local:
        return "private"
    return "public"


RANK = {"all": 3, "public": 2, "private": 1, "local": 0}


# ---------------- API ----------------
@router.get("/api/v1/ports")
async def ports(req: Request):
    proc = proc_dir(get_settings().host_proc)
    socks = host_sockets(proc)
    names = processes(proc, {s["inode"] for s in socks})
    docker = req.app.state.docker
    try:
        cs = await docker.containers()
    except DockerError:
        cs = []
    stack_of = {c["id"]: s["name"] for s in discover(cs, []) for c in s["containers"]}

    rows: dict[tuple, dict] = {}

    def row(port: int, proto: str) -> dict:
        return rows.setdefault((port, proto), {"port": port, "proto": proto, "addresses": [], "processes": [], "docker": [], "routes": []})

    for s in socks:
        r = row(s["port"], s["proto"])
        if s["ip"] not in r["addresses"]:
            r["addresses"].append(s["ip"])
        p = names.get(s["inode"])
        if p and p["name"] not in [x["name"] for x in r["processes"]]:
            r["processes"].append(p)
    for c in cs:
        for p in c.get("Ports") or []:
            if not p.get("PublicPort"):
                continue
            r = row(p["PublicPort"], p.get("Type") or "tcp")
            ip = p.get("IP") or "0.0.0.0"
            if ip not in r["addresses"]:
                r["addresses"].append(ip)
            entry = {"container": ctr_name(c), "service": (c.get("Labels") or {}).get("com.docker.compose.service") or ctr_name(c),
                     "stack": stack_of.get(c["Id"], ""), "private": p.get("PrivatePort"), "state": c.get("State"), "image": c.get("Image", "")}
            if entry not in r["docker"]:
                r["docker"].append(entry)

    # öffentliche Adressen über den Tunnel: Ziel http://IP:Port → Zeile mit diesem Port
    try:
        routes = await tunnels.routes(docker, cs)
    except DockerError:
        routes = []
    apps = []
    if cloudflare_api.enabled():
        try:
            apps = await cloudflare_api.access_apps()
        except cloudflare_api.CloudflareError:
            apps = []
    for t in routes:
        u = urlsplit(t["service"] or "")
        try:
            port = u.port or {"http": 80, "https": 443}.get(u.scheme)
        except ValueError:
            continue
        if not port or not u.hostname or (port, "tcp") not in rows:
            continue
        r = rows[(port, "tcp")]
        if not re.match(r"^[\d.]+$", u.hostname) and u.hostname not in ("localhost",):
            continue  # Ziel ist ein Container-Name im Docker-Netz, nicht dieser Host-Port
        if u.hostname not in r["addresses"] and not any(a in ANY for a in r["addresses"]):
            continue
        url = f"https://{t['host']}{t.get('path') or ''}"
        j = cloudflare_api.judge(url, apps) if apps else None
        r["routes"].append({"url": url, "access": j["state"] if j else "unknown", "detail": j["detail"] if j else ""})

    out = []
    for r in rows.values():
        r["addresses"].sort(key=lambda a: (":" in a, a))
        r["scope"] = max((scope(a) for a in r["addresses"]), key=lambda s: RANK[s], default="local")
        r["kind"] = "docker" if r["docker"] else "host"
        out.append(r)
    out.sort(key=lambda r: (r["port"], r["proto"]))
    return {"ports": out, "process_names": bool(names), "proc": str(proc)}


@router.get("/api/v1/ports/free")
async def free_ports(req: Request, start: int = 8440, end: int = 8499, count: int = 5):
    """Die nächsten freien TCP-Ports im Bereich – frei heißt: nichts lauscht und kein Container hat ihn veröffentlicht (auch gestoppte)."""
    if not (1 <= start <= end <= 65535) or end - start > 20000:
        raise HTTPException(400, "Bereich 1–65535, höchstens 20000 Ports")
    used = {s["port"] for s in host_sockets(proc_dir(get_settings().host_proc)) if s["proto"] == "tcp"}
    try:
        docker = req.app.state.docker
        for c in await docker.containers():
            used |= {p["PublicPort"] for p in c.get("Ports") or [] if p.get("PublicPort")}
            if c.get("State") != "running":  # gestoppte: Ports stehen nur in ihrer Konfiguration
                info = await docker.inspect(c["Id"])
                for b in ((info.get("HostConfig") or {}).get("PortBindings") or {}).values():
                    used |= {int(x["HostPort"]) for x in b or [] if str(x.get("HostPort", "")).isdigit()}
    except DockerError:
        pass
    free = [p for p in range(start, end + 1) if p not in used][:max(1, min(count, 50))]
    return {"start": start, "end": end, "free": free}


tool = Tool(id="ports", name="Ports", icon="lan", router=router)
