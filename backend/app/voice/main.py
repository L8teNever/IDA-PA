"""The voice container: `uvicorn app.voice.main:app` – same image as IDA, own container (docker-compose service „voice“).

Public (VOICE_PUBLIC_URL):  /telnyx/webhook (Ed25519 signature), /telnyx/media/{call}?k=… (per-call secret), /live?ticket=…
Internal (x-voice-secret):  /internal/calls, /internal/busy – used by IDA's stella_* tools
/health tells whether a conversation is running (the watchtower hook updates the container only when idle).
"""
import asyncio
import contextlib
import hashlib
import hmac
import logging

from fastapi import FastAPI, Header, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import get_settings
from . import web
from .phone import CallError, Phone
from .telnyx import TelnyxError, verify_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ida.voice")


class _NoCache(StaticFiles):
    """Static files of the live app: always re-validate (Cloudflare must not keep an old app.css for hours)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = "no-cache"
        return resp


def _version(front) -> str:
    h = hashlib.sha1()
    for d in ("css", "live"):
        for f in sorted(p for p in (front / d).rglob("*") if p.is_file()):
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


def create_app(phone: Phone | None = None, connect=None) -> FastAPI:
    s = get_settings()
    phone = phone or Phone(s, connect=connect)
    live: set[str] = set()

    async def reaper():
        while True:
            await asyncio.sleep(30)
            with contextlib.suppress(Exception):
                await phone.reap()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(reaper())
        log.info("Stella bereit (Gemini %s, Telnyx %s)", "ja" if s.gemini_api_key else "NEIN", "ja" if phone.telnyx.configured else "nein")
        yield
        task.cancel()

    app = FastAPI(title="IDA voice", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.phone, app.state.live = phone, live

    def secret_ok(given: str | None) -> bool:
        return bool(s.voice_secret and given and hmac.compare_digest(given, s.voice_secret))

    @app.get("/health")
    async def health():
        return {"ok": True, "busy": bool(phone.busy() or live), "calls": len(phone.busy()), "live": len(live),
                "gemini": bool(s.gemini_api_key), "telnyx": phone.telnyx.configured}

    @app.post("/internal/calls")
    async def place(request: Request, x_voice_secret: str | None = Header(None)):
        if not secret_ok(x_voice_secret):
            return JSONResponse({"detail": "Nicht erlaubt"}, status_code=403)
        b = await request.json()
        try:
            c = await phone.place(str(b.get("to") or ""), str(b.get("brief") or ""), bool(b.get("read_only")))
        except CallError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return {**c.view(), "hinweis": "Stella wählt. Ergebnis und Mitschrift später mit stella_call_status(call_id)."}

    @app.get("/internal/busy")
    async def busy(x_voice_secret: str | None = Header(None)):
        if not secret_ok(x_voice_secret):
            return JSONResponse({"detail": "Nicht erlaubt"}, status_code=403)
        return {"busy": bool(phone.busy() or live), "anrufe": [c.view() for c in phone.busy()], "live_gespraeche": len(live)}

    @app.post("/telnyx/webhook")
    @app.post("/webhooks/telnyx", include_in_schema=False)  # Pfad des alten Stella – so bleibt die URL in Telnyx gleich
    async def telnyx_webhook(request: Request):
        raw = await request.body()
        if not s.telnyx_public_key:
            log.warning("Telnyx-Webhook ohne TELNYX_PUBLIC_KEY abgelehnt")
            return JSONResponse({"detail": "TELNYX_PUBLIC_KEY fehlt"}, status_code=403)
        try:
            verify_signature(raw, request.headers.get("telnyx-timestamp", ""), request.headers.get("telnyx-signature-ed25519", ""), s.telnyx_public_key)
        except TelnyxError as e:
            log.warning("Telnyx-Webhook abgelehnt: %s", e)
            return JSONResponse({"detail": str(e)}, status_code=401)
        try:
            body = await request.json()
            await phone.webhook(body if isinstance(body, dict) else {})
        except Exception:  # noqa: BLE001 – Telnyx should not retry forever because of us
            log.exception("Telnyx-Webhook")
        return {"ok": True}

    @app.websocket("/telnyx/media/{call_id}")
    async def media(ws: WebSocket, call_id: str):
        call = phone.media_allowed(call_id, ws.query_params.get("k", ""))
        if not call:
            await ws.close(code=1008)
            return
        await ws.accept()
        await phone.media(ws, call)

    # Stand-alone live app (PWA) on the voice host: its own install, opens straight into the conversation.
    front = s.frontend_dir
    icon = "/static/live/icons"

    @app.get("/", include_in_schema=False)
    async def live_app():
        html = (front / "live" / "index.html").read_text(encoding="utf-8")
        v = _version(front)  # version in the asset links: a fresh deploy never meets an old cached copy
        html = html.replace('.css"', f'.css?v={v}"').replace('/live.js"', f'/live.js?v={v}"')
        return HTMLResponse(html, headers={"cache-control": "no-cache"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def live_manifest():
        return JSONResponse({
            "id": "/", "name": "IDA Live", "short_name": "IDA Live", "description": "Direkt mit Stella sprechen",
            "lang": "de", "start_url": "/", "scope": "/", "display": "fullscreen", "display_override": ["fullscreen", "standalone"],
            "background_color": "#000000", "theme_color": "#000000",
            "icons": [
                {"src": f"{icon}/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": f"{icon}/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": f"{icon}/maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
                {"src": f"{icon}/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        }, media_type="application/manifest+json", headers={"cache-control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    async def live_sw():
        return FileResponse(front / "sw.js", media_type="text/javascript", headers={"cache-control": "no-cache"})

    @app.post("/pwa/ticket", include_in_schema=False)
    async def pwa_ticket(x_live_key: str | None = Header(None)):
        """The live app trades its key for a one-minute ticket (same as IDA's /api/v1/voice/ticket)."""
        key = web.app_key(s.voice_secret)
        if not (key and x_live_key and hmac.compare_digest(x_live_key, key)):
            return JSONResponse({"detail": "Nicht erlaubt"}, status_code=403)
        return {"ticket": web.make_ticket(s.voice_secret), "name": "Stella"}

    @app.post("/pwa/phone", include_in_schema=False)
    async def pwa_phone(request: Request, x_live_key: str | None = Header(None)):
        """Button "Am Telefon weiterführen": Stella ruft Simons Nummer an und macht mit dem bisherigen Verlauf weiter."""
        key = web.app_key(s.voice_secret)
        if not (key and x_live_key and hmac.compare_digest(x_live_key, key)):
            return JSONResponse({"detail": "Nicht erlaubt"}, status_code=403)
        try:
            lines = (await request.json()).get("lines") or []
        except Exception:
            lines = []
        talk = "\n".join(f"{'Simon' if str(l.get('who')) == 'user' else 'Stella'}: {str(l.get('text') or '')[:600]}"
                         for l in lines[-30:] if isinstance(l, dict))
        brief = ("Simon hat gerade in der Live-App mit dir gesprochen und möchte das Gespräch jetzt am Telefon weiterführen. "
                 "Begrüße ihn kurz und mach nahtlos dort weiter, wo ihr aufgehört habt."
                 + (f"\n\nBisheriger Verlauf:\n{talk}" if talk else ""))
        try:
            c = await phone.place("", brief)
        except CallError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return {"ok": True, "call": c.id}

    for d in ("css", "fonts", "icons", "live"):  # only what the page needs
        app.mount(f"/static/{d}", _NoCache(directory=front / d), name=f"static-{d}")

    @app.websocket("/live")
    async def live_ws(ws: WebSocket):
        if not web.check_ticket(ws.query_params.get("ticket", ""), s.voice_secret):
            await ws.close(code=1008)
            return
        if len(live) >= 2:
            await ws.close(code=1013)
            return
        await ws.accept()
        await web.run(ws, s, ida=phone.ida, connect=connect, active=live)

    return app


app = create_app()
