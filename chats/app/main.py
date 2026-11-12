"""IDA Chats: deine Beeper-Chats im IDA-Stil – eigene App mit eigener PWA (docs/CHATS.md). Chats liegen in Beeper; chats.db merkt sich nur gesehene Nachrichten (store.py)."""
import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, live
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chats")


class Static(StaticFiles):
    def __init__(self, *, cache: str = "no-cache", **kw):
        super().__init__(**kw)
        self.cache = cache

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = self.cache if resp.status_code == 200 else "no-cache"
        return resp


def frontend_version(*dirs: Path) -> str:
    h = hashlib.sha1()
    for d in dirs:
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            h.update(str(f.relative_to(d)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(live.loop())
    log.info("Chats bereit")
    yield
    task.cancel()
    if api._beeper:
        await api._beeper.close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="IDA Chats", version="1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(api.router)

    front = s.frontend_dir
    own, css, fonts, js = front / "chats", front / "css", front / "fonts", front / "js" / "ui"
    if not own.exists():
        return app
    version = frontend_version(own, css, fonts, js)
    imm = "public, max-age=31536000, immutable"
    # alles unter /s/<version>/: ändert sich eine Datei, ändert sich die Adresse – kein Cache liefert Altes
    app.mount(f"/s/{version}/shared/css", Static(directory=css, cache=imm), name="shared-css")
    app.mount(f"/s/{version}/shared/fonts", Static(directory=fonts, cache=imm), name="shared-fonts")
    app.mount(f"/s/{version}/shared/js", Static(directory=js, cache=imm), name="shared-js")
    app.mount(f"/s/{version}", Static(directory=own, cache=imm), name="assets")
    app.mount("/icons", Static(directory=own / "icons", cache="public, max-age=86400"), name="icons")
    index_html = (own / "index.html").read_text(encoding="utf8").replace('"/static/', f'"/s/{version}/')

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest():
        icon, rev = "/icons", "?v=1"
        return JSONResponse({
            "id": "/chats-app", "name": "IDA Chats", "short_name": "Chats", "description": "Deine Chats aus Beeper im IDA-Stil",
            "lang": "de", "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#000000", "theme_color": "#000000", "categories": ["productivity"],
            "icons": [
                {"src": f"{icon}/icon-192.png{rev}", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": f"{icon}/icon-512.png{rev}", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": f"{icon}/maskable-192.png{rev}", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
                {"src": f"{icon}/maskable-512.png{rev}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        }, media_type="application/manifest+json", headers={"cache-control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker():
        return HTMLResponse((own / "sw.js").read_text(encoding="utf8"), media_type="text/javascript", headers={"cache-control": "no-cache"})

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith(("api/", "s/")):
            return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
        return HTMLResponse(index_html, headers={"cache-control": "no-store"})

    return app


app = create_app()
