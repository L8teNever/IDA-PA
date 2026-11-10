"""IDA Launcher: Startseite mit allen Apps, wie der Cloudflare-Access-Launcher – aber im IDA-Stil.

Die Liste kommt von selbst aus Cloudflare (Access-Apps + Tunnel-Adressen). Davor gehört Cloudflare Access.
"""
import hashlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from . import backgrounds, cloudflare, icons, status, usage
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("launcher")

app = FastAPI(title="IDA Launcher", docs_url=None, redoc_url=None, openapi_url=None)


def _fe() -> Path:
    return get_settings().frontend_dir


class Static(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = "no-cache"
        return resp


@app.get("/api/v1/health")
async def health():
    return {"ok": True}


def _view(a: dict, use: dict | None = None) -> dict:
    u = (use or {}).get(a["host"]) or {}
    h = hashlib.sha1(a["host"].encode()).hexdigest()[:8]
    st = status.read(a["host"]) or {}
    # IDA-Apps behalten ihren schönen Namen, bei allen anderen gilt der Titel der Website
    name = a["name"] if a.get("ida") or not st.get("title") else st["title"]
    return {"name": name, "up": st.get("up"), "host": a["host"], "url": a.get("url") or f"https://{a['host']}", "protected": a["protected"], "ida": bool(a.get("ida")), "n": u.get("n", 0), "last": u.get("last", 0),
            "icon": f"/api/v1/icon/{a['host']}?v={h}"}


@app.get("/api/v1/apps")
async def list_apps(refresh: bool = False):
    s = get_settings()
    if not s.cloudflare_api_token.strip():
        return JSONResponse({"title": s.title, "apps": [], "error": "CLOUDFLARE_API_TOKEN fehlt"})
    try:
        items = await cloudflare.apps(force=refresh)
    except cloudflare.CloudflareError as e:
        return JSONResponse({"title": s.title, "apps": [], "error": str(e)})
    use = usage.load()
    pending = status.warm(items)
    return {"title": s.title, "apps": [_view(a, use) for a in items], "error": "", "pending": pending}


class Visit(BaseModel):
    host: str


@app.post("/api/v1/visit")
async def visit(body: Visit):
    """Merkt sich, dass eine App geöffnet wurde (nur Adressen aus der Liste)."""
    try:
        items = await cloudflare.apps()
    except cloudflare.CloudflareError:
        raise HTTPException(404)
    host = body.host.strip().lower()
    if not any(x["host"] == host for x in items):
        raise HTTPException(404)
    usage.visit(host)
    return {"ok": True}


@app.get("/api/v1/icon/{host}")
async def icon(host: str):
    # nur Adressen aus der Liste (kein Zugriff auf beliebige Ziele)
    try:
        items = await cloudflare.apps()
    except cloudflare.CloudflareError:
        raise HTTPException(404)
    a = next((x for x in items if x["host"] == host.lower()), None)
    if not a:
        raise HTTPException(404)
    got = await icons.icon(a)
    if not got:
        raise HTTPException(404)
    return Response(got[0], media_type=got[1], headers={"cache-control": "private, max-age=86400", "x-content-type-options": "nosniff",
                                                         "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'"})


@app.get("/api/v1/backgrounds")
async def list_backgrounds():
    return {"items": [{"id": i, "url": f"/api/v1/background/{i}"} for i in backgrounds.ids()]}


@app.post("/api/v1/backgrounds")
async def add_background(request: Request):
    """Bild hochladen: der Body ist das Bild selbst (kein Formular)."""
    if int(request.headers.get("content-length") or 0) > backgrounds.MAX_BYTES:
        raise HTTPException(413, "Bild ist zu groß (max. 12 MB).")
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > backgrounds.MAX_BYTES:
            raise HTTPException(413, "Bild ist zu groß (max. 12 MB).")
    try:
        name = backgrounds.add(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": name, "url": f"/api/v1/background/{name}"}


@app.get("/api/v1/background/{name}")
async def background(name: str):
    p = backgrounds.path(name)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type=backgrounds.content_type(name),
                        headers={"cache-control": "private, max-age=31536000, immutable", "x-content-type-options": "nosniff",
                                 "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'"})


@app.delete("/api/v1/background/{name}")
async def delete_background(name: str):
    if not backgrounds.remove(name):
        raise HTTPException(404)
    return {"ok": True}


@app.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse({"name": "IDA Launcher", "short_name": "Apps", "start_url": "/", "display": "standalone",
                         "background_color": "#000000", "theme_color": "#000000",
                         "icons": [{"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                                   {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                                   {"src": "/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]},
                        media_type="application/manifest+json")


@app.get("/")
async def index():
    # Versionsnummer an CSS/JS hängen, damit nach einem Update nie alte Dateien aus dem Browser-Cache kommen
    root = _fe() / "launcher"
    v = max((int(f.stat().st_mtime) for f in (root / "js" / "launcher.js", root / "css" / "launcher.css") if f.exists()), default=0)
    html = (root / "index.html").read_text(encoding="utf-8").replace("__V__", str(v))
    return HTMLResponse(html, headers={"cache-control": "no-cache"})


app.mount("/static/shared/css", Static(directory=_fe() / "css", check_dir=False), name="shared-css")
app.mount("/static/shared/fonts", Static(directory=_fe() / "fonts", check_dir=False), name="shared-fonts")
app.mount("/icons", Static(directory=_fe() / "launcher" / "icons", check_dir=False), name="icons")
app.mount("/static", Static(directory=_fe() / "launcher", check_dir=False), name="static")
