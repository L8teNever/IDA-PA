"""Stella / live voice. Internal routes for the voice container (header x-voice-secret) and two for the web app:
a short-lived ticket for the live conversation in the browser and the list of recent calls."""
import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..services import voice_bridge
from ..services.errors import Invalid

router = APIRouter(tags=["voice"])
TICKET_S = 60


def make_ticket(secret: str, now: float | None = None) -> str:
    """exp.nonce.sig – the voice container checks it with the same secret (no call back to IDA needed)."""
    exp = int((now or time.time()) + TICKET_S)
    nonce = secrets.token_hex(8)
    sig = hmac.new(secret.encode(), f"{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{nonce}.{sig}"


def _denied():
    return JSONResponse({"detail": "Nicht erlaubt"}, status_code=403)


def _ok(secret: str | None) -> bool:
    try:
        voice_bridge.check_secret(secret)
        return True
    except Invalid:
        return False


@router.post("/api/v1/voice/internal/start", include_in_schema=False)
async def internal_start(request: Request, x_voice_secret: str | None = Header(None)):
    if not _ok(x_voice_secret):
        return _denied()
    b = await request.json()
    return await voice_bridge.start(str(b["call_id"]), str(b.get("direction") or "out"), str(b.get("number") or ""),
                                    bool(b.get("owner")), str(b.get("purpose") or ""))


@router.post("/api/v1/voice/internal/tool", include_in_schema=False)
async def internal_tool(request: Request, x_voice_secret: str | None = Header(None)):
    if not _ok(x_voice_secret):
        return _denied()
    b = await request.json()
    args = b.get("args") if isinstance(b.get("args"), dict) else {}
    return await voice_bridge.call_tool(str(b.get("call_id") or ""), str(b.get("name") or ""), args)


@router.post("/api/v1/voice/internal/event", include_in_schema=False)
async def internal_event(request: Request, x_voice_secret: str | None = Header(None)):
    if not _ok(x_voice_secret):
        return _denied()
    b = await request.json()
    await voice_bridge.event(str(b["call_id"]), str(b.get("direction") or "out"), str(b.get("number") or ""), str(b.get("status") or ""),
                             str(b.get("purpose") or ""), str(b.get("error") or ""), bool(b.get("owner")))
    return {"ok": True}


@router.post("/api/v1/voice/internal/finish", include_in_schema=False)
async def internal_finish(request: Request, x_voice_secret: str | None = Header(None)):
    if not _ok(x_voice_secret):
        return _denied()
    b = await request.json()
    return await voice_bridge.finish(str(b["call_id"]), b.get("transcript") or [], str(b.get("outcome") or ""), str(b.get("status") or "done"),
                                     str(b.get("error") or ""), str(b.get("direction") or "out"), str(b.get("number") or ""))


@router.post("/api/v1/voice/ticket")
async def ticket():
    """Start of a live conversation in the browser: where to connect, with a ticket valid for one minute."""
    s = get_settings()
    if not (s.voice_on and s.voice_public_url):
        raise Invalid("Live-Gespräch ist nicht eingerichtet (VOICE_URL, VOICE_SECRET, VOICE_PUBLIC_URL)")
    base = s.voice_public_url.rstrip("/")
    ws = "wss://" + base[8:] if base.startswith("https://") else "ws://" + base[7:] if base.startswith("http://") else base
    return {"url": f"{ws}/live?ticket={make_ticket(s.voice_secret)}"}


@router.get("/api/v1/voice/calls")
async def calls(limit: int = 20):
    return await voice_bridge.recent(limit)
