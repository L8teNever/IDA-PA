"""Das Whisper-Modell: lädt im Hintergrund, wandelt immer nur eine Datei gleichzeitig um (der Prozessor ist das Nadelöhr)."""
import io
import logging
import threading
import time

from .config import get_settings

log = logging.getLogger("whisper")

_lock = threading.Lock()
_state: dict = {"status": "aus", "error": "", "model": ""}
_model = None


def status() -> dict:
    return dict(_state)


def load() -> None:
    """Im Hintergrund aufrufen: holt das Modell (beim ersten Mal ~1,6 GB nach /data/hf) und wärmt es an."""
    global _model
    st = get_settings()
    _state.update(status="lädt", error="", model=st.whisper_model)
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        _model = WhisperModel(st.whisper_model, device="cpu", compute_type=st.whisper_compute, cpu_threads=max(1, st.whisper_threads))
        list(_model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1)[0])   # einmal durchlaufen lassen: danach ist alles im Speicher
        _state.update(status="bereit")
        log.info("Modell %s bereit (%s, %d Threads)", st.whisper_model, st.whisper_compute, st.whisper_threads)
    except Exception as e:  # noqa: BLE001 – der Dienst bleibt erreichbar, IDA Chats zeigt dann den Grund
        log.exception("Modell konnte nicht geladen werden")
        _state.update(status="fehler", error=str(e)[:300])


def transcribe(data: bytes, language: str = "") -> dict:
    """Sprachnachricht (Ogg/Opus, M4A, MP3, WebM …) → {text, language, duration, took}. Stille wird übersprungen (VAD), das verhindert
    erfundene Sätze in Pausen."""
    st = get_settings()
    t0 = time.monotonic()
    with _lock:
        segments, info = _model.transcribe(
            io.BytesIO(data), language=language or st.whisper_language or None, beam_size=max(1, st.whisper_beam),
            vad_filter=True, condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()   # erst hier läuft die eigentliche Arbeit
    return {"text": text, "language": info.language, "duration": round(info.duration, 1), "took": round(time.monotonic() - t0, 1)}
