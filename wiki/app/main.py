"""IDA Wiki: Eigenständiger Wissens-, Gedächtnis- und Chat-Archiv-Dienst mit PWA & externem MCP (docs/WIKI.md)."""
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
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, seed
from .api import files, history, internal, mcp, pages, search, sync
from .config import get_settings
from .errors import Conflict, Forbidden, Invalid, NotFound
from .services import backup, scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("wiki")
BASE = Path(__file__).resolve().parents[1]


class Static(StaticFiles):
    """Statische Dateien mit fester Cache-Regel."""

    def __init__(self, *, cache: str = "no-cache", **kw):
        super().__init__(**kw)
        self.cache = cache

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = self.cache if resp.status_code == 200 else "no-cache"
        return resp


def frontend_version(*dirs: Path) -> str:
    """Kurzer Hash über alle Dateien der Oberfläche für unveränderliches `/s/<version>/`-Caching."""
    h = hashlib.sha1()
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(p for p in d.rglob("*") if p.is_file() and "test" not in p.relative_to(d).parts[:1]):
            h.update(str(f.relative_to(d)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10] or "dev"


def shell_files(own: Path, css: Path, fonts: Path, version: str) -> list[str]:
    """Alle App-Shell-Dateien für die Offline-Fähigkeit im Service Worker."""
    out = ["/", "/manifest.webmanifest"]
    skip = {"sw.js", "index.html"}
    for d, prefix, only in (
        (own, f"/s/{version}", None),
        (css, f"/s/{version}/shared/css", None),
        (fonts, f"/s/{version}/shared/fonts", ".woff2"),
    ):
        if not d.exists():
            continue
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            rel = f.relative_to(d).as_posix()
            if (f.name in skip and d == own and "/" not in rel) or rel.startswith("test/") or (only and f.suffix != only) or f.name == "LICENSE.txt":
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
    log.info("IDA Wiki bereit")
    yield
    await scheduler.stop()
    await db.dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="IDA Wiki",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    for exc, code in ((Invalid, 400), (NotFound, 404), (Forbidden, 403), (Conflict, 409)):
        def make(status_code: int):
            async def handler(_: Request, e: Exception):
                return JSONResponse({"detail": str(e), **getattr(e, "extra", {})}, status_code=status_code)
            return handler
        app.add_exception_handler(exc, make(code))

    for r in (pages, history, files, search, sync, internal, mcp):
        app.include_router(r.router)

    if s.wiki_testing:
        t = APIRouter(prefix="/api/v1/_test")

        @t.post("/maintenance")
        async def run_maint(now: str | None = None):
            at = datetime.fromisoformat(now) if now else None
            return await scheduler.maintenance(at)

        @t.post("/backup")
        async def run_bkp():
            return await asyncio.to_thread(backup.run_backup)

        app.include_router(t)

    front = s.frontend_dir
    own, css, fonts = front / "wiki", front / "css", front / "fonts"
    version = frontend_version(own, css, fonts) if own.exists() else "dev"
    app.state.version = version

    if own.exists():
        imm = "public, max-age=31536000, immutable"
        if css.exists():
            app.mount(f"/s/{version}/shared/css", Static(directory=css, cache=imm), name="shared-css")
        if fonts.exists():
            app.mount(f"/s/{version}/shared/fonts", Static(directory=fonts, cache=imm), name="shared-fonts")
        app.mount(f"/s/{version}", Static(directory=own, cache=imm), name="assets")
        shell_json = json.dumps(shell_files(own, css, fonts, version))
        index = (own / "index.html").read_text(encoding="utf-8") if (own / "index.html").exists() else "<h1>IDA Wiki</h1>"
        index_html = index.replace('"/static/', f'"/s/{version}/')

        @app.get("/manifest.webmanifest", include_in_schema=False)
        async def manifest():
            return JSONResponse(
                build_manifest(version),
                media_type="application/manifest+json",
                headers={"cache-control": "no-cache"},
            )

        @app.get("/sw.js", include_in_schema=False)
        async def service_worker():
            sw = own / "sw.js"
            if not sw.exists():
                return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
            body = sw.read_text(encoding="utf-8").replace("__VERSION__", version).replace("__SHELL__", shell_json)
            return HTMLResponse(
                body,
                media_type="text/javascript",
                headers={"cache-control": "no-cache", "service-worker-allowed": "/"},
            )

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str, request: Request):
            if path.startswith(("api/", "mcp", "s/")):
                return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
            prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
            html = (
                index_html.replace('content="__BASE__"', f'content="{prefix}"').replace('"/s/', f'"{prefix}/s/')
                if prefix
                else index_html.replace('content="__BASE__"', 'content=""')
            )
            return HTMLResponse(html, headers={"cache-control": "no-store"})

    return app


def build_manifest(version: str) -> dict:
    icon = f"/s/{version}/icons"
    return {
        "id": "/wiki-app",
        "name": "IDA Wiki",
        "short_name": "Wiki",
        "description": "Persönliches Wiki, KI-Gedächtnis & Chat-Archiv – lokal, vernetzt, mit Ida",
        "lang": "de",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#000000",
        "theme_color": "#000000",
        "categories": ["productivity", "education"],
        "icons": [
            {"src": f"{icon}/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": f"{icon}/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Neue Notiz", "url": "/?neu=1", "icons": [{"src": f"{icon}/icon-192.png", "sizes": "192x192"}]},
            {"name": "Wissensgraph", "url": "/?ansicht=graph", "icons": [{"src": f"{icon}/icon-192.png", "sizes": "192x192"}]},
            {"name": "Verlauf", "url": "/?ansicht=verlauf", "icons": [{"src": f"{icon}/icon-192.png", "sizes": "192x192"}]},
        ],
    }


app = create_app()
