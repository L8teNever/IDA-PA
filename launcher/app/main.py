"""IDA Launcher: Startseite mit allen Apps, wie der Cloudflare-Access-Launcher – aber im IDA-Stil.

Die Liste kommt von selbst aus Cloudflare (Access-Apps + Tunnel-Adressen). Davor gehört Cloudflare Access.
"""
import hashlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import cloudflare, icons
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


def _view(a: dict) -> dict:
    h = hashlib.sha1(a["host"].encode()).hexdigest()[:8]
    return {"name": a["name"], "host": a["host"], "url": a.get("url") or f"https://{a['host']}", "protected": a["protected"], "ida": bool(a.get("ida")),
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
    return {"title": s.title, "apps": [_view(a) for a in items], "error": ""}


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
    return FileResponse(_fe() / "launcher" / "index.html", headers={"cache-control": "no-cache"})


app.mount("/static/shared/css", Static(directory=_fe() / "css", check_dir=False), name="shared-css")
app.mount("/static/shared/fonts", Static(directory=_fe() / "fonts", check_dir=False), name="shared-fonts")
app.mount("/icons", Static(directory=_fe() / "launcher" / "icons", check_dir=False), name="icons")
app.mount("/static", Static(directory=_fe() / "launcher", check_dir=False), name="static")
