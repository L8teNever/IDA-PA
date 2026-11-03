"""Stella / live voice. Internal routes for the voice container (header x-voice-secret) and two for the web app:
a short-lived ticket for the live conversation in the browser and the list of recent calls."""
import hashlib
import hmac
import secrets
import time

from pathlib import Path

from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..config import get_settings
from ..services import settings_store, voice_bridge
from ..services.errors import Invalid, NotFound
from .deps import DB

router = APIRouter(tags=["voice"])
from ..voice.web import app_key, make_ticket  # noqa: E402,F401 – make_ticket stays importable from here


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


@router.get("/api/v1/voice/app-link")
async def app_link():
    """Install link of the stand-alone live app on the voice host (contains the key; opening it once stores the key)."""
    s = get_settings()
    if not (s.voice_on and s.voice_public_url):
        raise Invalid("Live-Gespräch ist nicht eingerichtet (VOICE_URL, VOICE_SECRET, VOICE_PUBLIC_URL)")
    return {"url": f"{s.voice_public_url.rstrip('/')}/#key={app_key(s.voice_secret)}"}


@router.get("/api/v1/voice/calls")
async def calls(limit: int = 20):
    return await voice_bridge.recent(limit)


VOICE_CATALOG = [
    {"id": "Aoede", "name": "Aoede", "gender": "weiblich", "description": "Klar, nachdenklich, lebendig (Standard)"},
    {"id": "Kore", "name": "Kore", "gender": "weiblich", "description": "Ruhig, sanft, warm"},
    {"id": "Leda", "name": "Leda", "gender": "weiblich", "description": "Jugendlich, frisch, herzlich"},
    {"id": "Algieba", "name": "Algieba", "gender": "weiblich", "description": "Hell, sanft, fließend"},
    {"id": "Callirrhoe", "name": "Callirrhoe", "gender": "weiblich", "description": "Entspannt, natürlich, locker"},
    {"id": "Despina", "name": "Despina", "gender": "weiblich", "description": "Klar, direkt, ruhig"},
    {"id": "Pulcherrima", "name": "Pulcherrima", "gender": "weiblich", "description": "Präsent, selbstbewusst"},
    {"id": "Sulafat", "name": "Sulafat", "gender": "weiblich", "description": "Warm, vollmundig, weich"},
    {"id": "Vindemiatrix", "name": "Vindemiatrix", "gender": "weiblich", "description": "Zart, behutsam, leise"},
    {"id": "Achernar", "name": "Achernar", "gender": "weiblich", "description": "Sanft, zurückhaltend"},
    {"id": "Puck", "name": "Puck", "gender": "männlich", "description": "Aufgeweckt, dynamisch, verspielt"},
    {"id": "Charon", "name": "Charon", "gender": "männlich", "description": "Informativ, tiefenentspannt, ruhig"},
    {"id": "Fenrir", "name": "Fenrir", "gender": "männlich", "description": "Kräftig, energisch, bestimmt"},
    {"id": "Algenib", "name": "Algenib", "gender": "männlich", "description": "Markant, rau, souverän"},
    {"id": "Alnilam", "name": "Alnilam", "gender": "männlich", "description": "Fest, sachlich, standhaft"},
    {"id": "Enceladus", "name": "Enceladus", "gender": "männlich", "description": "Hauchig, intim, gelassen"},
    {"id": "Gacrux", "name": "Gacrux", "gender": "männlich", "description": "Reif, sonor, geerdet"},
    {"id": "Iapetus", "name": "Iapetus", "gender": "männlich", "description": "Klar, artikuliert, fokussiert"},
    {"id": "Rasalgethi", "name": "Rasalgethi", "gender": "männlich", "description": "Bedacht, informativ"},
    {"id": "Sadaltager", "name": "Sadaltager", "gender": "männlich", "description": "Erfahren, kompetent"},
    {"id": "Zubenelgenubi", "name": "Zubenelgenubi", "gender": "männlich", "description": "Lässig, ungezwungen"},
]


class VoiceUpdate(BaseModel):
    voice: str


@router.get("/api/v1/voice/settings")
async def get_voice_settings(db: DB):
    s = get_settings()
    current = await settings_store.get(db, "gemini_voice", s.gemini_voice or "Aoede")
    return {
        "voice": current,
        "voices": [
            {**v, "sample_url": f"/api/v1/voice/samples/{v['id']}"}
            for v in VOICE_CATALOG
        ],
    }


@router.patch("/api/v1/voice/settings")
async def update_voice_settings(body: VoiceUpdate, db: DB):
    target = body.voice.strip()
    match = next((v for v in VOICE_CATALOG if v["id"].lower() == target.lower()), None)
    if not match:
        raise Invalid(f"Unbekannte Stimme: {target}")
    await settings_store.put(db, "gemini_voice", match["id"])
    return {"ok": True, "voice": match["id"]}


@router.get("/api/v1/voice/samples/{voice_name}")
async def voice_sample(voice_name: str):
    s = get_settings()
    cleaned = voice_name.strip().lower()
    candidates = [
        s.frontend_dir / "audio" / "voices" / f"{cleaned}.wav",
        s.data_dir / "audio" / "voices" / f"{cleaned}.wav",
    ]
    for p in candidates:
        try:
            if p.is_file():
                return FileResponse(p, media_type="audio/wav")
        except (PermissionError, OSError):
            continue
    raise NotFound("Stimmprobe nicht gefunden")

