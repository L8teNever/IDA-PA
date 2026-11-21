"""Piper-Stimmen: Liste (voices.json von Hugging Face), Laden bei Bedarf ins Volume und Sprechen.

Eine Stimme besteht aus <key>.onnx und <key>.onnx.json. Was noch fehlt, lädt der Dienst beim ersten Gebrauch herunter;
danach bleibt es in PIPER_DIR. Im Speicher bleiben höchstens PIPER_KEEP Stimmen (die zuletzt benutzten).
"""
import io
import json
import logging
import re
import threading
import time
import wave
from collections import OrderedDict
from pathlib import Path

import httpx

from .config import get_settings

log = logging.getLogger("piper.voices")
KEY = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[\w]+-(x_low|low|medium|high)$")
CATALOG_MAX_AGE = 7 * 24 * 3600

# Zusätzliche Stimmen außerhalb von rhasspy/piper-voices (volle Adressen; Lizenz: nur privat, siehe jeweilige Seite)
HF = "https://huggingface.co"
EXTRA = {
    "de_DE-dii-high": {
        "name": "Dii", "language": {"code": "de_DE"}, "quality": "high", "num_speakers": 1,
        "files": {f"{HF}/Phoonnx/pipertts_de-DE_dii/resolve/main/dii_de-DE.onnx": {"size_bytes": 63511038},
                  f"{HF}/Phoonnx/pipertts_de-DE_dii/resolve/main/dii_de-DE.onnx.json": {}},
    },
    "de_DE-lila_hokuspokus-high": {
        "name": "Lila", "language": {"code": "de_DE"}, "quality": "high", "num_speakers": 1,
        "files": {f"{HF}/mahihossain/lila-piper-voices-de/resolve/main/de_DE-lila_hokuspokus-high.onnx": {"size_bytes": 114204024},
                  f"{HF}/mahihossain/lila-piper-voices-de/resolve/main/de_DE-lila_hokuspokus-high.onnx.json": {}},
    },
}

_loaded: "OrderedDict[str, object]" = OrderedDict()
_lock = threading.Lock()  # Laden und Sprechen nacheinander (eine Stimme rechnet ohnehin mit allen Kernen)
_catalog: dict = {}


class VoiceError(Exception):
    pass


def _dir() -> Path:
    d = Path(get_settings().piper_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def catalog() -> dict:
    """voices.json (alle Stimmen mit Sprache, Qualität, Dateien) – einmal geladen, im Volume zwischengespeichert."""
    global _catalog
    if _catalog:
        return _catalog
    f = _dir() / "voices.json"
    fresh = f.is_file() and time.time() - f.stat().st_mtime < CATALOG_MAX_AGE
    if not fresh:
        try:
            r = httpx.get(f"{get_settings().piper_voices_url}/voices.json", follow_redirects=True, timeout=30)
            r.raise_for_status()
            f.write_bytes(r.content)
        except Exception as e:  # noqa: BLE001 – ohne Netz gilt die alte Liste
            log.warning("Stimmenliste nicht ladbar: %s", e)
    try:
        _catalog = json.loads(f.read_text(encoding="utf8"))
    except (OSError, ValueError):
        _catalog = {}
    _catalog = {**_catalog, **EXTRA}
    return _catalog


def listing(lang: str = "") -> list[dict]:
    out = []
    for key, v in catalog().items():
        code = (v.get("language") or {}).get("code") or ""
        if lang and not code.lower().startswith(lang.lower()):
            continue
        size = sum(int(x.get("size_bytes") or 0) for p, x in (v.get("files") or {}).items() if p.endswith(".onnx"))
        out.append({"key": key, "name": v.get("name") or key, "language": code, "quality": v.get("quality") or "",
                    "speakers": int(v.get("num_speakers") or 1), "mb": round(size / 1e6), "downloaded": (_dir() / f"{key}.onnx").is_file()})
    return sorted(out, key=lambda x: x["key"])


def _download(key: str) -> Path:
    model = _dir() / f"{key}.onnx"
    if model.is_file() and (_dir() / f"{key}.onnx.json").is_file():
        return model
    entry = catalog().get(key)
    if not entry:
        raise VoiceError(f"Stimme {key} gibt es nicht")
    base = get_settings().piper_voices_url
    for path in entry.get("files") or {}:
        if not path.endswith((".onnx", ".onnx.json")):
            continue
        # immer unter dem Schlüssel ablegen (Zusatzstimmen heißen bei Hugging Face anders)
        target = _dir() / (f"{key}.onnx.json" if path.endswith(".json") else f"{key}.onnx")
        part = target.with_suffix(target.suffix + ".part")
        url = path if path.startswith("https://") else f"{base}/{path}"
        log.info("lade %s", url)
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with part.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
        part.replace(target)
    if not model.is_file():
        raise VoiceError(f"Stimme {key}: Modell fehlt")
    return model


def _voice(key: str):
    if not KEY.match(key or ""):
        raise VoiceError(f"Ungültiger Stimmenname: {key}")
    if key in _loaded:
        _loaded.move_to_end(key)
        return _loaded[key]
    from piper import PiperVoice
    v = PiperVoice.load(str(_download(key)))
    _loaded[key] = v
    while len(_loaded) > max(1, get_settings().piper_keep):
        _loaded.popitem(last=False)
    return v


def load(key: str) -> None:
    with _lock:
        _voice(key)


def speak(text: str, key: str, speed: float = 1.0) -> tuple[bytes, int]:
    """Text -> (PCM 16 bit mono, Abtastrate). speed > 1 = schneller."""
    from piper import SynthesisConfig
    speed = min(2.0, max(0.5, float(speed or 1.0)))
    with _lock:
        v = _voice(key)
        chunks = list(v.synthesize(text, SynthesisConfig(length_scale=1.0 / speed)))
    rate = chunks[0].sample_rate if chunks else int(v.config.sample_rate)
    return b"".join(c.audio_int16_bytes for c in chunks), rate


def wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def loaded() -> list[str]:
    return list(_loaded)
