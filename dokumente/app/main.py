"""Docs-Dienst: Dokumente schreiben (Markdown, DIN A4) mit Dateispeicher, API, Oberfläche und PWA (docs/DOCS.md)."""
import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, drive
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("docs")
BASE = Path(__file__).resolve().parents[1]


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


def shell_files(own: Path, css: Path, fonts: Path, version: str) -> list[str]:
    """Was die App offline braucht (der Service Worker legt es beim Installieren in den Cache)."""
    out = ["/", "/manifest.webmanifest"]
    for d, prefix, only in ((own, f"/s/{version}", None), (css, f"/s/{version}/shared/css", None), (fonts, f"/s/{version}/shared/fonts", ".woff2")):
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            rel = f.relative_to(d).as_posix()
            if (d == own and rel in ("sw.js", "index.html")) or (only and f.suffix != only):
                continue
            out.append(f"{prefix}/{rel}")
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(drive.worker())
    log.info("Docs bereit (Drive-Backup %s)", "an" if get_settings().drive_on else "aus")
    yield
    task.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="IDA Docs", version="1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(api.router)

    front = s.frontend_dir
    own, css, fonts = front / "docs", front / "css", front / "fonts"
    if not own.exists():
        return app
    version = frontend_version(own, css, fonts)
    imm = "public, max-age=31536000, immutable"
    # alles unter /s/<version>/: ändert sich eine Datei, ändert sich die Adresse – kein Cache liefert Altes
    app.mount(f"/s/{version}/shared/css", Static(directory=css, cache=imm), name="shared-css")
    app.mount(f"/s/{version}/shared/fonts", Static(directory=fonts, cache=imm), name="shared-fonts")
    app.mount(f"/s/{version}", Static(directory=own, cache=imm), name="assets")
    app.mount("/icons", Static(directory=own / "icons", cache="public, max-age=86400"), name="icons")
    shell_json = json.dumps(shell_files(own, css, fonts, version))
    index_html = (own / "index.html").read_text(encoding="utf8").replace('"/static/', f'"/s/{version}/')

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest():
        return JSONResponse(build_manifest(), media_type="application/manifest+json", headers={"cache-control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker():
        body = (own / "sw.js").read_text(encoding="utf8").replace("__VERSION__", version).replace("__SHELL__", shell_json)
        return HTMLResponse(body, media_type="text/javascript", headers={"cache-control": "no-cache", "service-worker-allowed": "/"})

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request):
        if path.startswith(("api/", "s/")):
            return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
        return HTMLResponse(index_html, headers={"cache-control": "no-store"})

    return app


def build_manifest() -> dict:
    icon, rev = "/icons", "?v=1"  # feste Adressen; rev nur hochzählen, wenn sich die Icons wirklich ändern
    return {
        "id": "/docs-app", "name": "IDA Docs", "short_name": "Docs", "description": "Dokumente schreiben – Markdown auf DIN A4",
        "lang": "de", "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#000000", "theme_color": "#000000", "categories": ["productivity"],
        "icons": [
            {"src": f"{icon}/icon-192.png{rev}", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/icon-512.png{rev}", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"{icon}/maskable-192.png{rev}", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": f"{icon}/maskable-512.png{rev}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


app = create_app()
