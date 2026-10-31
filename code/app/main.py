"""IDA Code: eigene App für den Code-Bereich (Projekte, Threads, Git, Vorschau) mit eigener Adresse und PWA.

Die Arbeit selbst macht IDA: dieselben KI-Accounts, Modelle, Projekte und GitHub-Anmeldung. Dieser Dienst liefert nur
die Oberfläche aus und reicht die nötigen API-Aufrufe an IDA weiter (Antworten werden gestreamt, nichts gepuffert).
"""
import hashlib
import json
import logging
import re
from urllib.parse import quote
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("code")

# Nur diese IDA-Endpunkte sind über die Code-App erreichbar (Pfad nach /api/v1/)
ALLOWED = [re.compile(p) for p in (
    r"bootstrap", r"usage",
    r"chat", r"chats/[^/]+", r"chats/[^/]+/(live|stop)",
    r"code(/.*)?", r"github(/.*)?",
    r"uploads(/[^/]+)?", r"files/[^/]+",
)]
# Kopfzeilen, die nur für eine einzelne Verbindung gelten (werden nicht weitergereicht)
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
       "host", "content-length"}


def allowed(path: str) -> bool:
    # keine Umwege wie code/../agents (würde beim Weiterreichen zu /api/v1/agents)
    if any(seg in (".", "..") for seg in path.replace("\\", "/").split("/")):
        return False
    return any(p.fullmatch(path) for p in ALLOWED)


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
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            h.update(str(f.relative_to(d)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:10] or "dev"


def shell_files(own: Path, css: Path, fonts: Path, version: str) -> list[str]:
    """Alle App-Shell-Dateien für den Service Worker (Oberfläche startet auch bei wackeligem Netz sofort)."""
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # lange Antworten (KI schreibt minutenlang): kein Lese-Timeout, nur beim Verbinden
    app.state.client = httpx.AsyncClient(base_url=get_settings().ida_internal_url.rstrip("/"),
                                         timeout=httpx.Timeout(connect=10, read=None, write=60, pool=10))
    log.info("IDA Code bereit (IDA unter %s)", get_settings().ida_internal_url)
    yield
    await app.state.client.aclose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="IDA Code", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/v1/health", include_in_schema=False)
    async def health():
        return {"ok": True}

    @app.get("/api/v1/app", include_in_schema=False)
    async def app_info():
        return {"ida_url": s.ida_public_url.rstrip("/"), "code_url": s.code_public_url.rstrip("/")}

    @app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def proxy(path: str, request: Request):
        if not allowed(path):
            return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
        client: httpx.AsyncClient = request.app.state.client
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        body = request.stream() if request.method in ("POST", "PUT", "PATCH") else None
        req = client.build_request(request.method, "/api/v1/" + quote(path, safe="/"), params=request.query_params.multi_items(),
                                   headers=headers, content=body)
        try:
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as e:
            log.warning("IDA nicht erreichbar: %s", e)
            return JSONResponse({"detail": "IDA ist gerade nicht erreichbar"}, status_code=502)
        out = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
        return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out, background=BackgroundTask(resp.aclose))

    front = s.frontend_dir
    own, css, fonts = front / "code", front / "css", front / "fonts"
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
        index_html = (own / "index.html").read_text(encoding="utf-8").replace('"/static/', f'"/s/{version}/')

        @app.get("/manifest.webmanifest", include_in_schema=False)
        async def manifest():
            return JSONResponse(build_manifest(version), media_type="application/manifest+json", headers={"cache-control": "no-cache"})

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


def build_manifest(version: str) -> dict:
    icon = f"/s/{version}/icons"
    return {
        "id": "/code-app",
        "name": "IDA Code",
        "short_name": "Code",
        "description": "Ida arbeitet in deinen GitHub-Projekten – Threads, Änderungen, Vorschau",
        "lang": "de",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#000000",
        "theme_color": "#000000",
        "categories": ["developer", "productivity"],
        "icons": [
            {"src": f"{icon}/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": f"{icon}/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


app = create_app()
