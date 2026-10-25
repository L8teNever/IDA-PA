"""App factory: API routers, background services and the single-page frontend."""
import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, seed
from .api import accounts, agents, chats, connectors, internal_mcp, memory, messages, routines, skills, system
from .config import get_settings
from .services import jobs, scheduler
from .services.errors import Invalid, NotFound

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ida")
BACKEND = Path(__file__).resolve().parents[1]


class Static(StaticFiles):
    """Static files with a fixed cache policy (default: re-validate on every load, ETag -> cheap 304)."""

    def __init__(self, *, cache: str = "no-cache", **kw):
        super().__init__(**kw)
        self.cache = cache

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = self.cache if resp.status_code == 200 else "no-cache"
        return resp


def frontend_version(front: Path) -> str:
    """Short hash over all frontend files: changes exactly when one of them changes."""
    h = hashlib.sha1()
    for f in sorted(p for p in front.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(front)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


def migrate() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(migrate)  # alembic runs its own event loop
    await seed.seed()
    jobs.start()
    if get_settings().scheduler_enabled:
        scheduler.start()
    if get_settings().whisper_preload:
        from .services import speech
        speech.warm_up()
    log.info("IDA bereit")
    yield
    await scheduler.stop()
    await jobs.stop()
    await db.dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="IDA", version="2.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.exception_handler(Invalid)
    async def invalid(_: Request, e: Invalid):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(NotFound)
    async def not_found(_: Request, e: NotFound):
        return JSONResponse({"detail": str(e)}, status_code=404)

    for r in (system, agents, chats, messages, routines, connectors, skills, memory, accounts, internal_mcp):
        app.include_router(r.router)

    front = s.frontend_dir
    # Files live under /s/<version>/ – the version changes with every update, so no cache in between
    # (browser, Cloudflare) can ever serve an old file, and unchanged versions may be cached for good.
    version = frontend_version(front)
    app.mount(f"/s/{version}", Static(directory=front, cache="public, max-age=31536000, immutable"), name="assets")
    app.mount("/static", Static(directory=front), name="static")  # unversioned (old links)
    index_html = (front / "index.html").read_text(encoding="utf8").replace('"/static/', f'"/s/{version}/')

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest():
        """Makes IDA installable as an app (PWA) with the mascot as icon."""
        icon = f"/s/{version}/icons"
        return JSONResponse({
            "id": "/", "name": "IDA", "short_name": "IDA", "description": "Deine persönliche Assistentin",
            "lang": "de", "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#000000", "theme_color": "#000000",
            "icons": [
                {"src": f"{icon}/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": f"{icon}/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": f"{icon}/maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
                {"src": f"{icon}/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
            "shortcuts": [
                {"name": "Chats", "url": "/chats", "icons": [{"src": f"{icon}/icon-192.png", "sizes": "192x192"}]},
                {"name": "Routinen", "url": "/routinen", "icons": [{"src": f"{icon}/icon-192.png", "sizes": "192x192"}]},
            ],
        }, media_type="application/manifest+json", headers={"cache-control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker():
        """Served from the root so it may control every page."""
        return FileResponse(front / "sw.js", media_type="text/javascript", headers={"cache-control": "no-cache"})

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        """Every page URL (/, /chats, /einstellungen/agents/lea …) gets the app; the browser router shows the page."""
        if path.startswith(("api/", "mcp/")):
            return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
        return HTMLResponse(index_html, headers={"cache-control": "no-store"})

    return app


app = create_app()
