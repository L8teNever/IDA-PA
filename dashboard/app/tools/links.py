"""Tool „Weiterleitungen“ (nur IDA Server): Kurzlinks anlegen und verwalten.

Weitergeleitet wird im öffentlichen Dienst „links“ (app/links_public.py), der nur die gemeinsame Link-Datenbank kennt. Dieses Tool
sitzt hinter Zero Trust und richtet auf Knopfdruck die Domains ein: Tunnel-Regel und DNS, bewusst OHNE Access – die Links sollen
ja jeder öffnen können. Jedes Kürzel gilt auf allen LINKS_DOMAINS.
Jeder Link bekommt eine Seite im Wiki-Gedächtnis (app/wiki_memory.py), damit die KIs wissen, was belegt ist.
"""
import asyncio
import re
import sqlite3
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .. import cloudflare_api as cf
from .. import links_store as ls
from .. import wiki_memory
from ..config import get_settings
from ..docker_api import DockerError
from . import Tool
from .publish import all_routes, host_ip, need_cf

router = APIRouter()

LABEL = "ida.links"  # am Dienst „links“: ida.links=public
PORT = 8000          # darauf lauscht er im Container


def store(app: FastAPI) -> ls.Store:
    if not hasattr(app.state, "links"):
        app.state.links = ls.Store(get_settings().links_db)
    return app.state.links


def home(link: dict) -> str:
    domains = get_settings().link_domains
    return link["domain"] if link["domain"] in domains else domains[0]


def view(link: dict, db: ls.Store) -> dict:
    """Für die Oberfläche; das Geräte-Cookie selbst verlässt die Datenbank nie."""
    main = home(link)
    out = {k: v for k, v in link.items() if k != "bound_device"}
    return {**out, "url": f"https://{main}/{link['code']}", "status": ls.status(link), "expires": ls.expires_at(link),
            "urls": [f"https://{d}/{link['code']}" for d in [main, *(x for x in get_settings().link_domains if x != main)]],
            "devices": db.device_count(link["id"]), "bound": ls.short(link.get("bound_device") or "")}


def clean_target(target: str) -> str:
    t = target.strip()
    if "://" not in t:
        t = "https://" + t
    try:
        u = urlsplit(t)
        u.port  # wirft bei „https://javascript:alert(1)“
    except ValueError:
        u = None
    if not u or u.scheme not in ("http", "https") or not u.hostname or re.search(r"[\s\x00-\x1f]", t):
        raise HTTPException(400, "Ziel muss eine Webadresse sein, z. B. https://example.com/seite")
    if u.hostname.lower() in get_settings().link_domains:
        raise HTTPException(400, "Ein Kurzlink darf nicht auf einen Kurzlink zeigen")
    return t


def clean_code(code: str) -> str:
    c = code.strip().lower()
    if not ls.CUSTOM_RE.match(c) or c in ls.RESERVED:
        raise HTTPException(400, "Eigenes Kürzel: 3–32 Zeichen, Buchstaben, Zahlen und - (nicht am Anfang oder Ende)")
    return c


class LinkIn(BaseModel):
    target: str = Field(max_length=2000)
    domain: str = ""
    code: str = ""                     # leer = zufällig (neu) / bleibt (ändern)
    note: str = Field("", max_length=200)
    enabled: bool = True
    max_uses: int | None = Field(None, ge=1, le=1_000_000)
    valid_from: int | None = None
    valid_until: int | None = None
    hours: float | None = Field(None, gt=0, le=87600)
    hours_from: Literal["create", "first"] = "create"
    device_mode: Literal["any", "once", "bind"] = "any"
    confirm: bool = False


def fields_of(body: LinkIn) -> dict:
    domains = get_settings().link_domains
    domain = body.domain.strip().lower() or domains[0]
    if domain not in domains:
        raise HTTPException(400, f"Unbekannte Domain – möglich: {', '.join(domains)}")
    if body.valid_from and body.valid_until and body.valid_from >= body.valid_until:
        raise HTTPException(400, "„Von“ muss vor „Bis“ liegen")
    return {"target": clean_target(body.target), "domain": domain, "note": body.note.strip(), "enabled": int(body.enabled),
            "max_uses": body.max_uses, "valid_from": body.valid_from, "valid_until": body.valid_until, "hours": body.hours,
            "hours_from": body.hours_from, "device_mode": body.device_mode, "confirm": int(body.confirm)}


def found(db: ls.Store, link_id: int) -> dict:
    link = db.get(link_id)
    if not link:
        raise HTTPException(404, "Diesen Link gibt es nicht")
    return link


async def upstream(app: FastAPI, routes: list[dict]) -> str:
    """Adresse, unter der der Tunnel den Dienst „links“ erreicht."""
    own = get_settings().links_upstream.strip()
    if own:
        return own.rstrip("/")
    try:
        containers = await app.state.docker.containers()
    except DockerError:
        containers = []
    for c in containers:
        if (c.get("Labels") or {}).get(LABEL):
            for p in c.get("Ports") or []:
                if p.get("PrivatePort") == PORT and p.get("PublicPort"):
                    ip = p.get("IP") or ""
                    if ip in ("", "0.0.0.0", "::"):
                        ip = host_ip(routes)
                    if ip and ip not in ("127.0.0.1", "::1") and ":" not in ip:
                        return f"http://{ip}:{p['PublicPort']}"
    raise HTTPException(400, "Weiß nicht, wie der Tunnel den Dienst „links“ erreicht – läuft er? Sonst LINKS_UPSTREAM setzen (z. B. http://10.7.0.1:8089) "
                             "oder LINKS_PORT auf eine IP binden, die cloudflared erreicht")


def domain_rows(routes: list[dict], apps: list[dict], up: str) -> list[dict]:
    out = []
    for d in get_settings().link_domains:
        r = next((x for x in routes if x["hostname"].lower() == d and not x["path"] and not x.get("shadowed_by")), None)
        access = cf.judge(f"https://{d}/", apps)["state"] == "access"
        out.append({"domain": d, "service": r["service"] if r else "", "ready": bool(r) and (not up or r["service"].rstrip("/") == up), "access": access})
    return out


@router.get("/api/v1/links")
async def overview(req: Request):
    db = store(req.app)
    out = {"links": [view(x, db) for x in db.all()], "domains": [{"domain": d, "ready": None} for d in get_settings().link_domains],
           "cloudflare": cf.enabled(), "tunnels": []}
    if not cf.enabled():
        return out
    try:
        tlist = await cf.tunnels()
        routes = await all_routes(tlist)
        apps = await cf.access_apps()
    except cf.CloudflareError as e:
        return {**out, "error": str(e)}
    try:
        up = await upstream(req.app, routes)
    except HTTPException as e:
        up, out["upstream_error"] = "", e.detail
    return {**out, "upstream": up, "domains": domain_rows(routes, apps, up), "tunnels": [{"id": t["id"], "name": t["name"]} for t in tlist if t["remote"]]}


@router.post("/api/v1/links")
async def create(body: LinkIn, req: Request):
    db = store(req.app)
    fields = fields_of(body)
    code = clean_code(body.code) if body.code.strip() else db.new_code()
    try:
        out = view(db.create({**fields, "code": code}), db)
        wiki_memory.kick(req.app)
        return out
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Das Kürzel „{code}“ ist schon vergeben")


class SetupIn(BaseModel):
    tunnel_id: str = ""
    replace: bool = False


@router.post("/api/v1/links/setup")
async def setup(body: SetupIn, req: Request):
    """Alle Link-Domains auf den Dienst „links“ zeigen lassen: Tunnel-Regel + DNS. Fremde Regeln bleiben, außer replace ist gesetzt."""
    need_cf()
    steps = []
    try:
        tlist = [t for t in await cf.tunnels() if t["remote"]]
        tunnel = next((t for t in tlist if t["id"] == body.tunnel_id), None) or (tlist[0] if tlist and not body.tunnel_id else None)
        if not tunnel:
            raise HTTPException(400, "Keinen passenden Cloudflare-Tunnel gefunden")
        routes = await all_routes(tlist)
        up = await upstream(req.app, routes)
        apps = await cf.access_apps()
    except cf.CloudflareError as e:
        raise HTTPException(502, str(e))
    for d in get_settings().link_domains:
        taken = next((r for r in routes if r["hostname"].lower() == d and r["service"].rstrip("/") != up), None)
        if taken and not body.replace:
            steps.append({"step": d, "ok": False, "detail": f"zeigt schon auf {taken['service']} – nichts geändert"})
            continue
        try:
            await cf.add_route(tunnel["id"], d, up)
            dns = await cf.point_to_tunnel(d, tunnel["id"])
        except cf.CloudflareError as e:
            steps.append({"step": d, "ok": False, "detail": str(e)})
            continue
        if cf.judge(f"https://{d}/", apps)["state"] == "access":  # sonst stünde vor jedem Link ein Login
            steps.append({"step": d, "ok": False, "detail": "Tunnel und DNS passen, aber eine Access-App schützt die Domain – Links verlangen so einen Login"})
        else:
            steps.append({"step": d, "ok": True, "detail": f"→ {up} (DNS: {dns_text(dns)})"})
    return {"ok": all(s["ok"] for s in steps), "steps": steps}


def dns_text(state: str) -> str:
    return {"created": "CNAME angelegt", "updated": "CNAME umgestellt", "unchanged": "passt schon"}.get(state, state)


@router.get("/api/v1/links/{link_id}")
async def details(link_id: int, req: Request):
    db = store(req.app)
    visits = [{**v, "device": ls.short(v["device"])} for v in db.visits(link_id)]
    return {**view(found(db, link_id), db), "visits": visits}


@router.put("/api/v1/links/{link_id}")
async def update(link_id: int, body: LinkIn, req: Request):
    db = store(req.app)
    found(db, link_id)
    fields = fields_of(body)
    if body.code.strip():
        fields["code"] = clean_code(body.code)
    try:
        out = view(db.update(link_id, fields), db)
        wiki_memory.kick(req.app)
        return out
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Das Kürzel „{fields.get('code')}“ ist schon vergeben")


class EnabledIn(BaseModel):
    enabled: bool


@router.post("/api/v1/links/{link_id}/enabled")
async def set_enabled(link_id: int, body: EnabledIn, req: Request):
    db = store(req.app)
    found(db, link_id)
    return view(db.update(link_id, {"enabled": int(body.enabled)}), db)


@router.post("/api/v1/links/{link_id}/reset")
async def reset(link_id: int, req: Request):
    db = store(req.app)
    found(db, link_id)
    return view(db.reset(link_id), db)


@router.delete("/api/v1/links/{link_id}")
async def remove(link_id: int, req: Request):
    if not store(req.app).delete(link_id):
        raise HTTPException(404, "Diesen Link gibt es nicht")
    wiki_memory.kick(req.app)
    return {"ok": True}


async def start(app: FastAPI) -> None:
    app.state.memory_task = asyncio.create_task(wiki_memory.loop(app))


async def stop(app: FastAPI) -> None:
    app.state.memory_task.cancel()


tool = Tool(id="links", name="Weiterleitungen", icon="link", router=router, start=start, stop=stop)
