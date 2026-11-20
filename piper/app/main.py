"""IDA Piper: Text -> Sprache lokal mit Piper (docs/PIPER.md). Nur im Stack erreichbar (kein Port nach außen);
Stella (voice) schickt hier ihre Sätze her, IDA holt die Stimmenliste und Hörproben für die Einstellungen."""
import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from . import voices
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("piper")


class Speak(BaseModel):
    text: str
    voice: str = ""
    speed: float = 1.0


def _preload() -> None:
    try:
        voices.load(get_settings().piper_default_voice)
        log.info("Stimme %s bereit", get_settings().piper_default_voice)
    except Exception as e:  # noqa: BLE001 – lädt dann beim ersten Satz
        log.warning("Standardstimme nicht geladen: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.autoload and get_settings().piper_preload:
        threading.Thread(target=_preload, daemon=True).start()
    yield


def create_app(autoload: bool = True) -> FastAPI:
    app = FastAPI(title="IDA Piper", version="1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.autoload = autoload

    @app.get("/api/v1/health", include_in_schema=False)
    async def health():
        return {"ok": True, "loaded": voices.loaded(), "default": get_settings().piper_default_voice}

    @app.get("/api/v1/voices")
    async def voice_list(lang: str = Query("", max_length=8, pattern=r"^[A-Za-z_]{0,8}$")):
        """Alle Stimmen (optional nur eine Sprache, z. B. lang=de) mit Qualität, Größe und ob sie schon geladen ist."""
        return {"voices": await asyncio.to_thread(voices.listing, lang), "default": get_settings().piper_default_voice}

    @app.post("/api/v1/tts")
    async def tts(body: Speak):
        """Antwort: WAV (16 bit mono). Die Abtastrate hängt an der Stimme (22050 bei high/medium, 16000 bei low)."""
        text = " ".join((body.text or "").split())
        if not text:
            raise HTTPException(400, "Text fehlt")
        if len(text) > get_settings().piper_max_chars:
            raise HTTPException(413, f"Text zu lang (höchstens {get_settings().piper_max_chars} Zeichen)")
        key = body.voice or get_settings().piper_default_voice
        try:
            pcm, rate = await asyncio.to_thread(voices.speak, text, key, body.speed)
        except voices.VoiceError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # noqa: BLE001
            log.exception("Sprechen fehlgeschlagen")
            raise HTTPException(500, f"Sprechen fehlgeschlagen: {e.__class__.__name__}") from e
        return Response(voices.wav(pcm, rate), media_type="audio/wav", headers={"x-sample-rate": str(rate)})

    return app


app = create_app()
