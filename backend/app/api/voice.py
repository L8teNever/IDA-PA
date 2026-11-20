"""Stella / live voice. Internal routes for the voice container (header x-voice-secret) and two for the web app:
a short-lived ticket for the live conversation in the browser and the list of recent calls."""
import hashlib
import hmac
import json
import secrets
import time

from pathlib import Path

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from ..config import get_settings
from ..services import settings_store, voice_brain, voice_bridge, voice_engine
from ..services.errors import Invalid, NotFound
from .deps import DB

router = APIRouter(tags=["voice"])
from ..voice.web import app_key, device_key, device_names, make_ticket  # noqa: E402,F401 – make_ticket stays importable from here


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
                                    bool(b.get("owner")), str(b.get("purpose") or ""), str(b.get("resume") or ""), str(b.get("mode") or ""),
                                    speaker=b.get("speaker") if isinstance(b.get("speaker"), dict) else None)


@router.post("/api/v1/voice/internal/recent", include_in_schema=False)
async def internal_recent(x_voice_secret: str | None = Header(None)):
    """The live app's list "Gespräch weiterführen"."""
    if not _ok(x_voice_secret):
        return _denied()
    return {"calls": await voice_bridge.resumable()}


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


@router.post("/api/v1/voice/internal/brain/open", include_in_schema=False)
async def internal_brain_open(request: Request, x_voice_secret: str | None = Header(None)):
    """Eigene Kette: startet Claude für dieses Gespräch (Anweisungen kommen vom voice-Container)."""
    if not _ok(x_voice_secret):
        return _denied()
    b = await request.json()
    try:
        return await voice_brain.open(str(b.get("call_id") or ""), str(b.get("instructions") or ""), bool(b.get("warm")))
    except (Invalid, NotFound) as e:
        return JSONResponse({"detail": str(e)}, status_code=400)


@router.post("/api/v1/voice/internal/brain/turn", include_in_schema=False)
async def internal_brain_turn(request: Request, x_voice_secret: str | None = Header(None)):
    """Eigene Kette: ein Satz rein, Claudes Antwort als NDJSON-Strom (eine JSON-Zeile pro Stück) zurück."""
    if not _ok(x_voice_secret):
        return _denied()
    b = await request.json()
    who = "system" if b.get("who") == "system" else "user"

    async def lines():
        async for ev in voice_brain.turn(str(b.get("call_id") or ""), str(b.get("text") or "")[:8000], who):
            yield json.dumps(ev, ensure_ascii=False) + "\n"
    return StreamingResponse(lines(), media_type="application/x-ndjson")


@router.post("/api/v1/voice/internal/brain/close", include_in_schema=False)
async def internal_brain_close(request: Request, x_voice_secret: str | None = Header(None)):
    if not _ok(x_voice_secret):
        return _denied()
    await voice_brain.close(str((await request.json()).get("call_id") or ""))
    return {"ok": True}


@router.post("/api/v1/voice/ticket")
async def ticket():
    """Start of a live conversation in the browser: where to connect, with a ticket valid for one minute."""
    s = get_settings()
    if not (s.voice_on and s.voice_public_url):
        raise Invalid("Live-Gespräch ist nicht eingerichtet (VOICE_URL, VOICE_SECRET, VOICE_PUBLIC_URL)")
    base = s.voice_public_url.rstrip("/")
    ws = "wss://" + base[8:] if base.startswith("https://") else "ws://" + base[7:] if base.startswith("http://") else base
    return {"url": f"{ws}/live?ticket={make_ticket(s.voice_secret)}"}


@router.get("/api/v1/voice/devices")
async def devices():
    """Lautsprecher (ida-speaker) aus VOICE_DEVICES mit ihren Schlüsseln – zum Eintragen auf der Einstellungsseite des Pi."""
    s = get_settings()
    if not s.voice_on:
        raise Invalid("Live-Gespräch ist nicht eingerichtet (VOICE_URL, VOICE_SECRET)")
    return {"devices": [{"name": n, "key": device_key(s.voice_secret, n)} for n in device_names(s.voice_devices)]}


@router.get("/api/v1/voice/app-link")
async def app_link():
    """Install link of the stand-alone live app on the voice host (contains the key; opening it once stores the key)."""
    s = get_settings()
    if not (s.voice_on and s.voice_public_url):
        raise Invalid("Live-Gespräch ist nicht eingerichtet (VOICE_URL, VOICE_SECRET, VOICE_PUBLIC_URL)")
    base = (s.voice_app_url or s.voice_public_url).rstrip("/")
    return {"url": f"{base}/#key={app_key(s.voice_secret)}"}


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
    voice: str | None = None  # Gemini-Stimme
    engine: str | None = None  # gemini | pipeline
    stt: str | None = None
    llm: str | None = None
    tts: str | None = None
    piper_voice: str | None = None
    piper_speed: float | None = None


async def _engine_view(db) -> dict:
    from ..services import providers
    names = await providers.model_names(db)
    return {**await voice_engine.get(db), "engines": voice_engine.ENGINES, "stt_models": voice_engine.STT_MODELS,
            "tts_engines": voice_engine.TTS_ENGINES, "llm_models": [{"id": m, "name": names.get(m, m)} for m in providers.claude_models()],
            "status": voice_engine.status()}


@router.get("/api/v1/voice/settings")
async def get_voice_settings(db: DB):
    current = await settings_store.get(db, "gemini_voice", "Aoede")
    return {
        "voice": current,
        "voices": [
            {**v, "sample_url": f"/api/v1/voice/samples/{v['id']}"}
            for v in VOICE_CATALOG
        ],
        "engine": await _engine_view(db),
    }


@router.patch("/api/v1/voice/settings")
async def update_voice_settings(body: VoiceUpdate, db: DB):
    out: dict = {"ok": True}
    if body.voice is not None:
        target = body.voice.strip()
        match = next((v for v in VOICE_CATALOG if v["id"].lower() == target.lower()), None)
        if not match:
            raise Invalid(f"Unbekannte Stimme: {target}")
        await settings_store.put(db, "gemini_voice", match["id"])
        out["voice"] = match["id"]
    patch = body.model_dump(exclude={"voice"}, exclude_none=True)
    if patch:
        await voice_engine.update(db, patch)
        out["engine"] = await _engine_view(db)
    return out


@router.get("/api/v1/voice/piper/voices")
async def piper_voices(lang: str = "de"):
    """Stimmen des Piper-Dienstes (Standard: nur Deutsch)."""
    s = get_settings()
    if not s.piper_url:
        raise Invalid("Piper ist nicht eingerichtet (PIPER_URL)")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(s.piper_url.rstrip("/") + "/api/v1/voices", params={"lang": lang[:8]})
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise Invalid(f"Piper ist gerade nicht erreichbar ({e.__class__.__name__})") from e
    return r.json()


PIPER_SAMPLE = "Hallo Simon, so klinge ich. Dein nächster Termin ist morgen um acht Uhr dreißig."


@router.get("/api/v1/voice/piper/sample/{voice}")
async def piper_sample(voice: str, speed: float = 1.0):
    """Hörprobe einer Piper-Stimme (lädt sie beim ersten Mal herunter – das kann dauern)."""
    s = get_settings()
    if not s.piper_url:
        raise Invalid("Piper ist nicht eingerichtet (PIPER_URL)")
    if not voice_engine.PIPER_KEY.match(voice):
        raise Invalid(f"Ungültige Piper-Stimme: {voice}")
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(s.piper_url.rstrip("/") + "/api/v1/tts", json={"text": PIPER_SAMPLE, "voice": voice, "speed": speed})
    except httpx.HTTPError as e:
        raise Invalid(f"Piper ist gerade nicht erreichbar ({e.__class__.__name__})") from e
    if r.status_code != 200:
        raise Invalid(f"Piper: {r.text[:200]}")
    return Response(r.content, media_type="audio/wav", headers={"cache-control": "private, max-age=86400"})


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

