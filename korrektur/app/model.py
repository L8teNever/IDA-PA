"""Das T5-Modell: lädt im Hintergrund, korrigiert Stück für Stück (immer nur eine Anfrage gleichzeitig)."""
import logging
import re
import threading
from collections import OrderedDict

from .config import get_settings
from .filter import merge

log = logging.getLogger("korrektur")
SENT = re.compile(r"(?<=[.!?…])\s+")

_lock = threading.Lock()
_state: dict = {"status": "aus", "error": ""}
_tok = _model = None
_cache: OrderedDict[str, dict] = OrderedDict()


def status() -> dict:
    return dict(_state)


def load() -> None:
    """Im Hintergrund aufrufen: holt das Modell (beim ersten Mal ~1 GB nach /data/hf) und wärmt es an."""
    global _tok, _model
    st = get_settings()
    _state.update(status="lädt", error="")
    try:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        torch.set_num_threads(max(1, st.korrektur_threads))
        _tok = AutoTokenizer.from_pretrained(st.korrektur_model)
        _model = AutoModelForSeq2SeqLM.from_pretrained(st.korrektur_model).eval()
        generate("hallo wie gehts")
        _state.update(status="bereit")
        log.info("Modell %s bereit", st.korrektur_model)
    except Exception as e:  # noqa: BLE001 – der Dienst bleibt erreichbar, die App zeigt dann einfach keine Vorschläge
        log.exception("Modell konnte nicht geladen werden")
        _state.update(status="fehler", error=str(e)[:300])


def generate(text: str) -> str:
    import torch
    ids = _tok(text, return_tensors="pt", truncation=True, max_length=256).input_ids
    with torch.inference_mode():
        # Kurz halten: das Modell wiederholt sonst gern den ganzen Satz – und jedes Token kostet Zeit
        out = _model.generate(ids, max_new_tokens=int(ids.shape[1] * 1.2) + 3, num_beams=1)
    return _tok.decode(out[0], skip_special_tokens=True)


def chunks(line: str, n: int) -> list[str]:
    """Eine Zeile in Stücke von höchstens n Wörtern, möglichst an Satzenden."""
    out: list[str] = []
    for sent in SENT.split(line):
        words = sent.split()
        out += [" ".join(words[i:i + n]) for i in range(0, len(words), n)]
    return [c for c in out if c]


def fix(text: str) -> str:
    """Modell-Ausgabe für den ganzen Text (Zeilen und Stücke einzeln), noch ungefiltert."""
    n = get_settings().korrektur_chunk_words
    return "\n".join(" ".join(generate(c) for c in chunks(line, n)) for line in text.split("\n"))


def correct(text: str) -> dict:
    if text in _cache:
        _cache.move_to_end(text)
        return _cache[text]
    with _lock:
        res = merge(text, fix(text))
    _cache[text] = res
    if len(_cache) > 300:
        _cache.popitem(last=False)
    return res
