"""IDA Dashboard: eigene App (eigener Port, eigene PWA) für den Server – Docker-Stacks, Leistung, Speicher.

Liest die Docker-Engine über den Socket, misst den Server über /host/proc und speichert den Verlauf in
data/dashboard.db. Die Funktionen sind Tools (app/tools/), damit später weitere dazukommen können.
"""
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .docker_api import Docker, make_client
from .jobs import Jobs
from .store import Store
from .tools import NAV, all_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dashboard")
logging.getLogger("httpx").setLevel(logging.WARNING)  # sonst jede Docker-Abfrage (alle 15 s × alle Container) im Log


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
    h = hashlib.sha1()
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            h.update(str(f.relative_to(d)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10] or "dev"


def shell_files(own: Path, css: Path, fonts: Path, version: str) -> list[str]:
    out = ["/", "/manifest.webmanifest"]
    for d, prefix, only in ((own, f"/s/{version}", None), (css, f"/s/{version}/shared/css", None), (fonts, f"/s/{version}/shared/fonts", ".woff2")):
        if not d.exists():
            continue
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            rel = f.relative_to(d).as_posix()
            if (d == own and rel in ("sw.js", "index.html")) or (only and f.suffix != only):
                continue
            out.append(f"{prefix}/{rel}")
    return out


def create_app(*, docker: Docker | None = None, store: Store | None = None, background: bool = True) -> FastAPI:
    s = get_settings()
    tools = all_tools()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        own_client = None
        if docker is None:
            own_client = make_client(s.docker_host)
            app.state.docker = Docker(own_client)
        if store is None:
            app.state.store = Store(s.data_dir / "dashboard.db", s.sample_seconds, s.raw_keep_hours, s.keep_days)
        for t in tools:
            if t.start and background:
                await t.start(app)
        log.info("%s bereit (Docker: %s, Tools: %s)", "IDA Server" if s.is_server else "IDA Dashboard", s.docker_host, ", ".join(t.id for t in tools))
        yield
        for t in reversed(tools):
            if t.stop and background:
                await t.stop(app)
        for task in list(app.state.jobs.tasks):
            task.cancel()
        if own_client:
            await own_client.aclose()
        if store is None:
            app.state.store.close()

    app = FastAPI(title="IDA Dashboard", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.jobs = Jobs()
    if docker is not None:
        app.state.docker = docker
    if store is not None:
        app.state.store = store

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # Schutz vor fremden Seiten (CSRF): ändernde Aufrufe nur als JSON – das kann ein fremdes Formular nicht schicken,
        # und fetch() von fremden Seiten scheitert an der CORS-Vorabfrage.
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
            # Hochladen schickt rohe Daten: dort schützt der eigene Header genauso, fremde Seiten dürfen ihn nicht setzen.
            upload = request.url.path == "/api/v1/fs/upload" and request.method == "PUT" and request.headers.get("x-ida-upload") == "1"
            if not upload and not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"detail": "Nur JSON"}, status_code=415)
        return await call_next(request)

    @app.get("/api/v1/health", include_in_schema=False)
    async def health():
        return {"ok": True}

    @app.get("/api/v1/app", include_in_schema=False)
    async def app_info():
        return {"ida_url": s.ida_public_url.rstrip("/"), "dashboard_url": s.dashboard_public_url.rstrip("/"),
                "server_url": s.server_public_url.rstrip("/"),
                "mode": "server" if s.is_server else "dashboard", "title": "IDA Server" if s.is_server else "IDA Dashboard",
                "nav": NAV["server" if s.is_server else "dashboard"],
                "tools": [{"id": t.id, "name": t.name, "icon": t.icon} for t in tools]}

    for t in tools:
        app.include_router(t.router)

    front = s.frontend_dir
    own, css, fonts = front / "dashboard", front / "css", front / "fonts"
    version = frontend_version(own, css, fonts) if own.exists() else "dev"
    app.state.version = version
    if own.exists():
        imm = "public, max-age=31536000, immutable"
        if css.exists():
            app.mount(f"/s/{version}/shared/css", Static(directory=css, cache=imm), name="shared-css")
        if fonts.exists():
            app.mount(f"/s/{version}/shared/fonts", Static(directory=fonts, cache=imm), name="shared-fonts")
        app.mount(f"/s/{version}", Static(directory=own, cache=imm), name="assets")
        # Icons unter fester Adresse: das Manifest ändert sich so nicht bei jedem Update (sonst baut Android die installierte App über den Play Store neu)
        app.mount("/icons", Static(directory=own / "icons", cache="public, max-age=86400"), name="icons")
        shell_json = json.dumps(shell_files(own, css, fonts, version))
        index_html = (own / "index.html").read_text(encoding="utf-8").replace('"/static/', f'"/s/{version}/')
        if s.is_server:
            index_html = index_html.replace("<title>IDA Dashboard</title>", "<title>IDA Server</title>")

        @app.get("/manifest.webmanifest", include_in_schema=False)
        async def manifest():
            return JSONResponse(build_manifest(version, s.is_server), media_type="application/manifest+json", headers={"cache-control": "no-cache"})

        @app.get("/sw.js", include_in_schema=False)
        async def service_worker():
            body = (own / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", version).replace("__SHELL__", shell_json)
            return HTMLResponse(body, media_type="text/javascript", headers={"cache-control": "no-cache", "service-worker-allowed": "/"})

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith(("api/", "s/")):
                return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
            return HTMLResponse(index_html, headers={"cache-control": "no-store"})

    return app


def build_manifest(version: str, server: bool = False) -> dict:
    # feste Adressen; ICON_REV nur hochzählen, wenn sich die Icons wirklich ändern
    icon, rev = "/icons", "?v=2"
    return {
        "id": "/server-app" if server else "/dashboard-app",
        "name": "IDA Server" if server else "IDA Dashboard",
        "short_name": "Server" if server else "Dashboard",
        "description": "Websites, Veröffentlichen, Stacks und Dateien deines Servers einrichten" if server else "Docker-Stacks, Leistung und Speicher deines Servers",
        "lang": "de",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#000000",
        "categories": ["utilities", "productivity"],
        "icons": [
            {"src": f"{icon}/icon-192.png{rev}", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/icon-512.png{rev}", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/maskable-192.png{rev}", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": f"{icon}/maskable-512.png{rev}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


app = create_app()
