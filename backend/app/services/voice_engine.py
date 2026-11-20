"""Wie Stella spricht (Einstellungen → Stella), gespeichert unter dem Schlüssel „voice_engine“:

  gemini:   Gemini Live macht alles in einem (hören, denken, sprechen) – schnell, aber teuer (Audio-Tokens, auch für Stille).
  pipeline: die eigene Kette – Cloudflare Workers AI wandelt das Gesagte in Text, ein Claude-Modell der Accounts antwortet
            (Standard Haiku), Piper spricht die Antwort lokal. Nur Gesagtes kostet etwas, das Freikontingent von Cloudflare reicht weit.
"""
import re

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from . import providers, settings_store
from .errors import Invalid

KEY = "voice_engine"
ENGINES = [
    {"id": "gemini", "name": "Gemini Live", "description": "Alles in einem: hört, denkt und spricht selbst. Am schnellsten, kostet aber Google-Guthaben – auch für Stille."},
    {"id": "pipeline", "name": "Eigene Kette", "description": "Cloudflare erkennt die Sprache, Claude antwortet, Piper spricht. Kostet nur das Gesagte, dafür etwas langsamer."},
]
# Neurons pro Audiominute laut Cloudflare (10.000 Neurons am Tag sind frei)
STT_MODELS = [
    {"id": "@cf/openai/whisper-large-v3-turbo", "name": "Whisper large-v3-turbo", "neurons": 47,
     "description": "Genau, versteht Deutsch gut. Rund 210 Gratis-Minuten am Tag."},
    {"id": "@cf/deepgram/nova-3", "name": "Deepgram Nova-3", "neurons": 473,
     "description": "Sehr schnell, rund 21 Gratis-Minuten am Tag."},
    {"id": "@cf/openai/whisper", "name": "Whisper (älter)", "neurons": 41,
     "description": "Das ältere Whisper-Modell ohne Sprachvorgabe – nur als Ausweichmöglichkeit."},
]
TTS_ENGINES = [
    {"id": "piper", "name": "Piper (lokal)", "description": "Läuft im Stack auf dem Prozessor, kostet nichts. Stimme frei wählbar."},
]
DEFAULT_PIPER_VOICE = "de_DE-thorsten-high"
PIPER_KEY = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[\w]+-(x_low|low|medium|high)$")


def default_llm() -> str:
    ids = providers.claude_models()
    return next((m for m in ids if "haiku" in m), None) or next((m for m in get_settings().model_list if "haiku" in m), "claude-haiku-5-5")


def _clean(raw: dict | None) -> dict:
    r = raw if isinstance(raw, dict) else {}
    out = {
        "engine": r.get("engine") if r.get("engine") in {e["id"] for e in ENGINES} else "gemini",
        "stt": r.get("stt") if r.get("stt") in {m["id"] for m in STT_MODELS} else STT_MODELS[0]["id"],
        "llm": str(r.get("llm") or "") or default_llm(),
        "tts": r.get("tts") if r.get("tts") in {t["id"] for t in TTS_ENGINES} else "piper",
        "piper_voice": r.get("piper_voice") if PIPER_KEY.match(str(r.get("piper_voice") or "")) else DEFAULT_PIPER_VOICE,
    }
    try:
        out["piper_speed"] = round(min(1.5, max(0.7, float(r.get("piper_speed") or 1.0))), 2)
    except (TypeError, ValueError):
        out["piper_speed"] = 1.0
    return out


async def get(db: AsyncSession) -> dict:
    return _clean(await settings_store.get(db, KEY, None))


async def update(db: AsyncSession, patch: dict) -> dict:
    cur = await get(db)
    for k in ("engine", "stt", "llm", "tts", "piper_voice", "piper_speed"):
        if k not in patch or patch[k] is None:
            continue
        v = patch[k]
        if k == "engine" and v not in {e["id"] for e in ENGINES}:
            raise Invalid(f"Unbekannter Modus: {v}")
        if k == "stt" and v not in {m["id"] for m in STT_MODELS}:
            raise Invalid(f"Unbekanntes Erkennungsmodell: {v}")
        if k == "tts" and v not in {t["id"] for t in TTS_ENGINES}:
            raise Invalid(f"Unbekannte Sprachausgabe: {v}")
        if k == "llm":
            v = str(v)
            if providers.of_model(v) != "claude":
                raise Invalid("Für Stella geht hier nur ein Claude-Modell")
            providers.check_model(v)
        if k == "piper_voice" and not PIPER_KEY.match(str(v)):
            raise Invalid(f"Ungültige Piper-Stimme: {v}")
        cur[k] = v
    cur = _clean(cur)
    await settings_store.put(db, KEY, cur)
    return cur


def status() -> dict:
    """Was für die Kette eingerichtet ist (ohne Geheimnisse)."""
    s = get_settings()
    return {"cloudflare": bool(s.cf_ai_account and s.cf_ai_token), "piper": bool(s.piper_url)}
