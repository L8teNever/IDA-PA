"""Health selbst im Cloudflare Mesh: Der Container hat WARP (warp-svc startet in entrypoint.sh, wenn /dev/net/tun da ist).
„Health ins Mesh aufnehmen“ legt einen Mesh-Knoten an, verbindet WARP damit und nimmt die Mesh-IP. Die Adresse
http://<mesh-ip>:8000 geht an alle Geräte (config.mesh_server): Wer selbst im Mesh ist, schickt dann alles darüber.

Sicherheit: Über die Mesh-IP ist nur der Agent-Pfad erreichbar (siehe MeshGuard in main.py) – Oberfläche und API nicht,
denn dort steht kein Cloudflare Access davor.
"""
import asyncio
import ipaddress
import logging
import shutil
from pathlib import Path

from . import mesh
from .config import get_settings

log = logging.getLogger("health.selfmesh")

MESH_NET = ipaddress.ip_network("100.64.0.0/10")   # Cloudflare Mesh nutzt 100.96.0.0/12 (liegt darin)
PORT = 8000                                        # Port im Container (Mesh-Verkehr kommt direkt an, ohne Docker-Port)
STATE: dict = {"ip": "", "status": "", "detail": "", "addrs": set()}


def in_mesh(addr: str) -> bool:
    """Gehört diese (lokale) Adresse zum Mesh? 100.64.0.0/10 oder irgendeine Adresse (auch IPv6) der WARP-Schnittstelle."""
    if addr in STATE["addrs"]:
        return True
    try:
        return ipaddress.ip_address(addr) in MESH_NET
    except ValueError:
        return False


async def _run(*argv: str, timeout: float = 30) -> tuple[int, str]:
    try:
        p = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as e:
        return 127, str(e)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124, "Zeitüberschreitung"
    return p.returncode or 0, out.decode(errors="replace").strip()


def _warp(*args: str, timeout: float = 30):
    return _run("warp-cli", "--accept-tos", *args, timeout=timeout)


async def _ip() -> str:
    """IPv4 der WARP-Schnittstelle; merkt sich alle ihre Adressen (auch IPv6) für den MeshGuard."""
    code, out = await _run("ip", "-o", "addr", "show", "CloudflareWARP", timeout=5)
    if code:
        STATE["addrs"] = set()
        return ""
    addrs, v4 = set(), ""
    words = out.split()
    for i, w in enumerate(words[:-1]):
        if w in ("inet", "inet6"):
            a = words[i + 1].split("/")[0]
            addrs.add(a)
            if w == "inet" and not v4:
                v4 = a
    STATE["addrs"] = addrs
    return v4


async def status() -> dict:
    """{available, reason, status, ip, address, node, detail}"""
    s = get_settings()
    out = {"available": False, "reason": "", "status": "nicht eingerichtet", "ip": "", "address": "", "node": s.mesh_node,
           "detail": "", "manual": bool(s.mesh_address)}
    if not shutil.which("warp-cli"):
        out["reason"] = "In diesem Image ist kein WARP installiert."
    elif not Path("/dev/net/tun").exists():
        out["reason"] = "Der Container darf kein VPN: in der Compose-Datei beim Dienst health cap_add: [NET_ADMIN] und devices: [/dev/net/tun] ergänzen."
    else:
        code, st = await _warp("status")
        out["detail"] = st
        if code:
            out["reason"] = f"WARP-Dienst im Container antwortet nicht (fehlt NET_ADMIN?): {st[:200]}"
        else:
            out["available"] = True
            low = st.lower()
            if "connected" in low and "disconnected" not in low:
                out["status"] = "verbunden"
            elif "registration missing" in low:
                out["status"] = "nicht eingerichtet"
            else:
                out["status"] = "getrennt"
            out["ip"] = await _ip()
    out["address"] = address(out["ip"] if out["status"] == "verbunden" else "")
    STATE.update(ip=out["ip"], status=out["status"], detail=out["detail"])
    return out


def address(ip: str = "") -> str:
    """Adresse, die die Geräte bekommen: von Hand gesetzt, sonst die Mesh-IP von Health. Solange ein Knoten eingerichtet ist,
    bleibt die zuletzt gesehene IP – auch wenn WARP gerade kurz getrennt ist (sonst vergäßen die Geräte die Adresse).
    Leer nur, wenn Health wirklich nicht im Mesh ist (kein Knoten)."""
    s = get_settings()
    if s.mesh_address:
        return s.mesh_address
    ip = ip or (STATE["ip"] if STATE["status"] == "verbunden" else "")
    if not ip and s.mesh_node:
        ip = s.mesh_ip
    return f"http://{ip}:{PORT}" if ip else ""


async def join(save) -> dict:
    """Mesh-Knoten für Health anlegen, WARP damit verbinden. save(values) speichert Einstellungen (mesh_node)."""
    st = await status()
    if not st["available"]:
        raise mesh.MeshError(st["reason"])
    s = get_settings()
    old = s.mesh_node
    node, token = await mesh.create_node("ida-health")
    await _warp("disconnect")
    await _warp("registration", "delete")
    code, out = await _warp("connector", "new", token, timeout=60)
    if code:
        await mesh.delete_node(node)
        raise mesh.MeshError(f"WARP nimmt den Knoten nicht an: {out}")
    save({"mesh_node": node})
    if old and old != node:
        try:
            await mesh.delete_node(old)
        except Exception as e:  # noqa: BLE001 – alter Knoten bleibt dann eben liegen
            log.warning("alter Mesh-Knoten %s: %s", old, e)
    await _warp("connect")
    for _ in range(30):
        await asyncio.sleep(1)
        st = await status()
        if st["status"] == "verbunden" and st["ip"]:
            save({"mesh_ip": st["ip"]})
            break
    return st


async def leave(save) -> dict:
    s = get_settings()
    await _warp("disconnect")
    await _warp("registration", "delete")
    if s.mesh_node:
        await mesh.delete_node(s.mesh_node)
    save({"mesh_node": "", "mesh_ip": ""})
    return await status()


async def watch(hub, save, every: float = 60) -> None:
    """Im Hintergrund: Mesh-IP von Health verfolgen (neue IP merken) und die Adresse an alle Geräte schicken."""
    while True:
        try:
            if shutil.which("warp-cli"):
                st = await status()
                s = get_settings()
                if st["status"] == "verbunden" and st["ip"] and s.mesh_node and st["ip"] != s.mesh_ip:
                    save({"mesh_ip": st["ip"]})
            await hub.set_mesh_server(address())
        except Exception:  # noqa: BLE001
            log.exception("Mesh-Status von Health")
        await asyncio.sleep(every)
