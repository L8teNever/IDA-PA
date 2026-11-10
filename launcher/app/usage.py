"""Nutzung der Apps: wie oft und wann zuletzt jede Adresse geöffnet wurde (für „Oft genutzt“ und „Zuletzt genutzt“).

Liegt als kleine JSON-Datei im Volume, gilt also für alle Geräte gleich.
"""
import json
import os
import threading
import time

from .config import get_settings

_lock = threading.Lock()


def _file():
    d = get_settings().data_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / "usage.json"


def load() -> dict:
    try:
        data = json.loads(_file().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def visit(host: str) -> None:
    with _lock:
        data = load()
        e = data.get(host) or {"n": 0, "last": 0}
        data[host] = {"n": int(e.get("n", 0)) + 1, "last": int(time.time())}
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, _file())
