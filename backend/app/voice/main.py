"""The voice container: `uvicorn app.voice.main:app` – same image as IDA, own container (docker-compose service „voice“).

Public (VOICE_PUBLIC_URL):  /telnyx/webhook (Ed25519 signature), /telnyx/media/{call}?k=… (per-call secret), /live?ticket=…
Internal (x-voice-secret):  /internal/calls, /internal/busy – used by IDA's stella_* tools
/health tells whether a conversation is running (the watchtower hook updates the container only when idle).
"""
import asyncio
import contextlib
import hmac
import logging

from fastapi import FastAPI, Header, Request, WebSocket
from fastapi.responses import JSONResponse

from ..config import get_settings
from . import web
from .phone import CallError, Phone
from .telnyx import TelnyxError, verify_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ida.voice")


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
