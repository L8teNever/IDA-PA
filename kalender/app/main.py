"""Kalender-Dienst: eigene App mit eigener Datenbank, API, Oberfläche und PWA (docs/KALENDER.md)."""
import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, seed
from .api import calendars, events, extra, ics, misc
from .config import get_settings
from .errors import Conflict, Forbidden, Invalid, NotFound
from .services import alarms, outbox, scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kalender")
BASE = Path(__file__).resolve().parents[1]


class Static(StaticFiles):
    """Statische Dateien mit fester Cache-Regel (Standard: bei jedem Laden prüfen, ETag → billiges 304)."""

    def __init__(self, *, cache: str = "no-cache", **kw):
        super().__init__(**kw)
        self.cache = cache

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = self.cache if resp.status_code == 200 else "no-cache"
        return resp


def frontend_version(*dirs: Path) -> str:
    """Kurzer Hash über alle Dateien der Oberfläche: ändert sich genau dann, wenn sich eine Datei ändert."""
    h = hashlib.sha1()
    for d in dirs:
        for f in sorted(p for p in d.rglob("*") if p.is_file() and "test" not in p.relative_to(d).parts[:1]):  # Tests gehören nicht zur App-Version
            h.update(str(f.relative_to(d)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


def shell_files(own: Path, css: Path, fonts: Path, version: str) -> list[str]:
    """Alle Dateien, die die App offline braucht (der Service Worker legt sie beim Installieren in den Cache)."""
    out = ["/", "/manifest.webmanifest"]
    skip = {"sw.js", "index.html"}
    for d, prefix, only in ((own, f"/s/{version}", None), (css, f"/s/{version}/shared/css", None), (fonts, f"/s/{version}/shared/fonts", ".woff2")):
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            rel = f.relative_to(d).as_posix()
            if f.name in skip and d == own and "/" not in rel or rel.startswith("test/") or (only and f.suffix != only) or f.name == "LICENSE.txt":
                continue
            out.append(f"{prefix}/{rel}")
    return out


def migrate() -> None:
    cfg = Config(str(BASE / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE / "alembic"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(migrate)
    await seed.seed()
    if get_settings().scheduler_enabled:
        scheduler.start()
    log.info("Kalender bereit")
    yield
    await scheduler.stop()
    await db.dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="IDA Kalender", version="1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

    for exc, code in ((Invalid, 400), (NotFound, 404), (Forbidden, 403), (Conflict, 409)):
        def make(code):
            async def handler(_: Request, e):
                return JSONResponse({"detail": str(e)}, status_code=code)
            return handler
        app.add_exception_handler(exc, make(code))

    for r in (calendars, events, extra, misc, ics):
        app.include_router(r.router)

    if s.kalender_testing:
        t = APIRouter(prefix="/api/v1/_test")

        @t.post("/tick")
        async def tick(now: str | None = None):
            at = datetime.fromisoformat(now) if now else None
            return {"fired": await alarms.tick(at), "sent": await outbox.flush(at)}

        @t.post("/maintenance")
        async def maint():
            await scheduler.maintenance()
            return {"ok": True}

        app.include_router(t)

    front = s.frontend_dir
    own, css, fonts = front / "kalender", front / "css", front / "fonts"
    version = frontend_version(own, css, fonts) if own.exists() else "dev"
    app.state.version = version
    if own.exists():
        imm = "public, max-age=31536000, immutable"
        # Dateien liegen unter /s/<version>/ – ändert sich etwas, ändert sich die Version, kein Zwischenspeicher kann Altes liefern
        app.mount(f"/s/{version}/shared/css", Static(directory=css, cache=imm), name="shared-css")
        app.mount(f"/s/{version}/shared/fonts", Static(directory=fonts, cache=imm), name="shared-fonts")
        app.mount(f"/s/{version}", Static(directory=own, cache=imm), name="assets")
        shell_json = json.dumps(shell_files(own, css, fonts, version))
        index = (own / "index.html").read_text(encoding="utf8") if (own / "index.html").exists() else "<h1>IDA Kalender</h1>"
        index_html = index.replace('"/static/', f'"/s/{version}/')

        @app.get("/manifest.webmanifest", include_in_schema=False)
        async def manifest():
            return JSONResponse(build_manifest(version), media_type="application/manifest+json", headers={"cache-control": "no-cache"})

        @app.get("/sw.js", include_in_schema=False)
        async def service_worker():
            sw = own / "sw.js"
            if not sw.exists():
                return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
            body = sw.read_text(encoding="utf8").replace("__VERSION__", version).replace("__SHELL__", shell_json)
            return HTMLResponse(body, media_type="text/javascript", headers={"cache-control": "no-cache", "service-worker-allowed": "/"})

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str, request: Request):
            if path.startswith(("api/", "ics/", "mcp", "dav/", "s/")):
                return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
            prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")  # hinter dem IDA-Proxy: /kal
            html = index_html.replace('content="__BASE__"', f'content="{prefix}"').replace('"/s/', f'"{prefix}/s/') if prefix else index_html.replace('content="__BASE__"', 'content=""')
            return HTMLResponse(html, headers={"cache-control": "no-store"})

    return app


def build_manifest(version: str) -> dict:
    icon = f"/s/{version}/icons"
    return {
        "id": "/kalender-app", "name": "IDA Kalender", "short_name": "Kalender", "description": "Dein Kalender – lokal, mit Ida",
        "lang": "de", "start_url": "/", "scope": "/", "display": "standalone", "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#000000", "theme_color": "#000000", "categories": ["productivity"],
        "icons": [
            {"src": f"{icon}/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": f"{icon}/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Neuer Termin", "url": "/?neu=1", "icons": [{"src": f"{icon}/icon-192.png", "sizes": "192x192"}]},
            {"name": "Heute", "url": "/?heute=1", "icons": [{"src": f"{icon}/icon-192.png", "sizes": "192x192"}]},
        ],
    }


app = create_app()
