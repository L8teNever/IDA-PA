"""Speech to text, locally with Whisper (faster-whisper) – the recording never leaves the server.

The model (WHISPER_MODEL, default "small") is downloaded once into data/whisper and stays loaded.
Transcriptions run one after another in a worker thread so the server stays responsive.
"""
import asyncio
import logging
import tempfile
import threading
import time
from pathlib import Path

from ..config import get_settings
from .errors import Invalid

log = logging.getLogger("ida.speech")
MAX_BYTES = 30 * 1024 * 1024
_model = None
_model_lock = threading.Lock()
_run_lock = asyncio.Lock()


def _load():
    global _model
    with _model_lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:  # pragma: no cover – the image always has it
                raise Invalid("Spracherkennung ist nicht installiert (faster-whisper)") from e
            s = get_settings()
            cache = s.data_dir / "whisper"
            cache.mkdir(parents=True, exist_ok=True)
            t0 = time.monotonic()
            _model = WhisperModel(s.whisper_model, device="cpu", compute_type=s.whisper_compute_type, download_root=str(cache))
            log.info("whisper model %s loaded in %.1fs", s.whisper_model, time.monotonic() - t0)
    return _model


def transcribe_file(path: str) -> dict:
    """Blocking: audio file -> {"text", "seconds", "language"}."""
    s = get_settings()
    model = _load()
    segments, info = model.transcribe(path, language=s.whisper_language or None, vad_filter=True, beam_size=5,
                                      condition_on_previous_text=False)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return {"text": text, "seconds": round(info.duration, 1), "language": info.language}


async def transcribe(data: bytes, suffix: str = ".webm") -> dict:
    if not data:
        raise Invalid("Keine Aufnahme erhalten")
    if len(data) > MAX_BYTES:
        raise Invalid("Aufnahme zu groß (max. 30 MB)")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        path = f.name
    try:
        async with _run_lock:
            t0 = time.monotonic()
            out = await asyncio.to_thread(transcribe_file, path)
            out["ms"] = int((time.monotonic() - t0) * 1000)
            return out
    except Invalid:
        raise
    except Exception as e:  # noqa: BLE001 – broken/unsupported audio
        log.warning("transcription failed: %s", e)
        raise Invalid(f"Aufnahme konnte nicht umgewandelt werden: {e.__class__.__name__}") from e
    finally:
        Path(path).unlink(missing_ok=True)


def warm_up() -> None:
    """Load the model in the background after start (first use is then fast)."""
    threading.Thread(target=lambda: _safe_load(), daemon=True, name="whisper-load").start()


def _safe_load() -> None:
    try:
        _load()
    except Exception as e:  # noqa: BLE001
        log.warning("whisper model not loaded: %s", e)
