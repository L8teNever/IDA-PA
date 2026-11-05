"""Tool „Veröffentlichen“ (nur IDA Server): Dienste direkt über den Cloudflare-Tunnel ins Netz bringen.

Je Adresse in einem Schritt: Tunnel-Regel (Hostname[/Pfad] → Ziel), DNS-CNAME auf den Tunnel und auf Wunsch Zero Trust
(Access-App mit erlaubten E-Mails). Ziele schlägt das Tool aus den Containern mit veröffentlichten Ports vor. Entfernen
nimmt die Regel raus und – wenn sonst nichts mehr darauf zeigt – den DNS-Eintrag.
Alles über die Cloudflare-API mit CLOUDFLARE_API_TOKEN (wie der Cloudflare-MCP).
"""
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import cloudflare_api as cf
from .. import tunnels
from ..config import get_settings
from ..discovery import ctr_name, discover
from ..docker_api import DockerError
from . import Tool

router = APIRouter()

HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
SERVICE_RE = re.compile(r"^((https?|tcp|ssh|rdp|smb|unix)://\S+|http_status:\d{3}|hello_world)$")
LOCAL = ("127.0.0.1", "localhost", "::1")


def need_cf():
    if not cf.enabled():
        raise HTTPException(400, "CLOUDFLARE_API_TOKEN fehlt – ohne Token kann der Server nichts veröffentlichen")


def fail(e: Exception):
    raise HTTPException(502, str(e))


def clean_host(h: str) -> str:
    h = (h or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/").rstrip(".")
    if not HOST_RE.match(h):
        raise HTTPException(400, f"„{h}“ ist kein gültiger Hostname (z. B. app.kulbarts.com)")
    return h


def clean_path(p: str) -> str:
    p = (p or "").strip()
    if not p or p == "/":
        return ""
    if not p.startswith("/") or " " in p:
        raise HTTPException(400, "Pfad muss mit / anfangen, z. B. /admin")
    return p


def host_ip(routes: list[dict]) -> str:
    """IP des Servers, wie der Tunnel ihn erreicht: eingestellt oder aus den vorhandenen Regeln (http://10.7.0.1:8080 …)."""
    own = get_settings().tunnel_host_ip.strip()
    if own:
        return own
    for r in routes:
        h = urlsplit(r.get("service") or "").hostname or ""
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", h) and h not in LOCAL:
            return h
    return ""


async def all_routes(tlist: list[dict]) -> list[dict]:
    out = []
    for t in tlist:
        if not t["remote"]:
            continue
        cfg = await cf.tunnel_config(t["id"])
        first: dict[tuple, dict] = {}
        for i, r in enumerate(cfg.get("ingress") or []):
            if not r.get("hostname"):
                continue
            row = {"tunnel_id": t["id"], "tunnel": t["name"], "hostname": r["hostname"], "path": r.get("path") or "",
                   "service": r.get("service") or "", "index": i}
            key = (r["hostname"].lower(), row["path"])
            if key in first:  # cloudflared nimmt immer die erste passende Regel – diese hier greift nie
                row["shadowed_by"] = first[key]["service"]
                row["shadowed_host"] = first[key]["hostname"]
            else:
                first[key] = row
            out.append(row)
    return out


def targets(containers: list[dict], ip: str) -> list[dict]:
    """Vorschläge für Ziele: jeder Container-Port, der auf dem Server veröffentlicht ist."""
    stacks = {c["id"]: s["name"] for s in discover(containers, []) for c in s["containers"]}
    out = []
    for c in containers:
        if c.get("State") != "running":
            continue
        for p in c.get("Ports") or []:
            if not p.get("PublicPort") or p.get("Type", "tcp") != "tcp" or p.get("IP") in LOCAL:
                continue
            bind = p.get("IP") if p.get("IP") not in (None, "", "0.0.0.0", "::") else ip
            if not bind or ":" in bind:
                continue
            out.append({"stack": stacks.get(c["Id"], ""), "container": ctr_name(c), "port": p["PublicPort"], "private": p.get("PrivatePort"),
                        "service": f"http://{bind}:{p['PublicPort']}"})
    out.sort(key=lambda x: (x["stack"], x["container"], x["port"]))
    return list({x["service"]: x for x in out}.values())


@router.get("/api/v1/publish")
async def overview(req: Request):
    if not cf.enabled():
        return {"enabled": False, "routes": [], "tunnels": [], "targets": []}
    try:
        tlist = await cf.tunnels()
        routes = await all_routes(tlist)
        apps = await cf.access_apps()
    except cf.CloudflareError as e:
        return {"enabled": True, "error": str(e), "routes": [], "tunnels": [], "targets": []}
    try:
        containers = await req.app.state.docker.containers()
    except DockerError:
        containers = []
    idx = tunnels.indexes(containers)
    stacks = {c["id"]: s["name"] for s in discover(containers, []) for c in s["containers"]}
    for r in routes:
        url = f"https://{r['hostname']}{r['path']}"
        r["url"] = url
        r["access"] = cf.judge(url, apps)
        c = tunnels.target_of(r["service"], idx)
        r["container"] = ctr_name(c) if c else ""
        r["stack"] = stacks.get(c["Id"], "") if c else ""
    ip = host_ip(routes)
    return {"enabled": True, "tunnels": tlist, "routes": routes, "host_ip": ip, "targets": targets(containers, ip)}


class PublishIn(BaseModel):
    tunnel_id: str
    hostname: str
    path: str = ""
    service: str = Field(max_length=500)
    zero_trust: bool = False
    emails: list[str] = Field(default_factory=list, max_length=50)
    replace: bool = False


@router.post("/api/v1/publish")
async def publish(body: PublishIn):
    need_cf()
    host, path, service = clean_host(body.hostname), clean_path(body.path), body.service.strip()
    if not SERVICE_RE.match(service):
        raise HTTPException(400, "Ziel z. B. http://10.7.0.1:8080, http://container:80 oder http_status:404")
    steps = []
    try:
        cur = [r for r in (await cf.tunnel_config(body.tunnel_id)).get("ingress") or [] if r.get("hostname") == host and (r.get("path") or "") == path]
        if cur and cur[0].get("service") != service and not body.replace:
            raise HTTPException(409, f"{host}{path} zeigt schon auf {cur[0].get('service')} – ersetzen?")
        await cf.add_route(body.tunnel_id, host, service, path)
        steps.append({"step": "Tunnel-Regel", "ok": True, "detail": f"{host}{path} → {service}"})
        dns = await cf.point_to_tunnel(host, body.tunnel_id)
        steps.append({"step": "DNS", "ok": True, "detail": {"created": "CNAME angelegt", "updated": "CNAME umgestellt", "unchanged": "CNAME passt schon"}[dns]})
        if body.zero_trust:
            res = await cf.protect(host + path, body.emails, name=host + path)
            steps.append({"step": "Zero Trust", "ok": True, "detail": ("Access-App angelegt" if res["neu"] else "Policy zur vorhandenen App") + f": {', '.join(body.emails)}"})
    except cf.CloudflareError as e:
        steps.append({"step": "Fehler", "ok": False, "detail": str(e)})
    return {"ok": all(s["ok"] for s in steps), "url": f"https://{host}{path}", "steps": steps}


class UnpublishIn(BaseModel):
    tunnel_id: str
    hostname: str
    path: str = ""
    dns: bool = True
    access: bool = False


@router.post("/api/v1/publish/remove")
async def unpublish(body: UnpublishIn):
    need_cf()
    host, path = clean_host(body.hostname), clean_path(body.path)
    steps = []
    try:
        removed = await cf.remove_route(body.tunnel_id, host, path)
        steps.append({"step": "Tunnel-Regel", "ok": True, "detail": "entfernt" if removed else "gab es nicht"})
        others = [r for r in (await cf.tunnel_config(body.tunnel_id)).get("ingress") or [] if (r.get("hostname") or "").lower() == host]
        if body.dns and not others:
            steps.append({"step": "DNS", "ok": True, "detail": "CNAME gelöscht" if await cf.remove_dns(host, body.tunnel_id) else "kein CNAME auf diesen Tunnel"})
        if body.access:
            steps.append({"step": "Zero Trust", "ok": True, "detail": "Access-App gelöscht" if await cf.unprotect(host + path) else "keine eigene Access-App"})
    except cf.CloudflareError as e:
        steps.append({"step": "Fehler", "ok": False, "detail": str(e)})
    return {"ok": all(s["ok"] for s in steps), "steps": steps}


class RuleIn(BaseModel):
    tunnel_id: str
    index: int
    hostname: str
    service: str


@router.post("/api/v1/publish/remove-rule")
async def remove_rule(body: RuleIn):
    """Genau eine Tunnel-Regel entfernen (z. B. ein Duplikat, das nie greift). DNS und Access bleiben, die Adresse geht weiter."""
    need_cf()
    try:
        await cf.remove_rule(body.tunnel_id, body.index, body.hostname, body.service)
    except cf.CloudflareError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


class AccessIn(BaseModel):
    hostname: str
    path: str = ""
    enabled: bool
    emails: list[str] = Field(default_factory=list, max_length=50)


@router.post("/api/v1/publish/access")
async def set_access(body: AccessIn):
    """Zero Trust für eine Adresse an (Access-App mit diesen E-Mails) oder aus (Access-App weg)."""
    need_cf()
    domain = clean_host(body.hostname) + clean_path(body.path)
    try:
        if body.enabled:
            res = await cf.protect(domain, body.emails, name=domain)
            return {"ok": True, "detail": "Access-App angelegt" if res["neu"] else "Policy zur vorhandenen App hinzugefügt"}
        if not await cf.unprotect(domain):
            raise HTTPException(404, "Für genau diese Adresse gibt es keine eigene Access-App (evtl. schützt eine Wildcard-App sie – das in Cloudflare ändern)")
        return {"ok": True, "detail": "Access-App gelöscht – die Adresse ist jetzt ohne Login erreichbar"}
    except cf.CloudflareError as e:
        fail(e)


tool = Tool(id="publish", name="Veröffentlichen", icon="travel_explore", router=router)
