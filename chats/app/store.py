"""Merkzettel für Nachrichten (SQLite in DATA_DIR): Was der Dienst einmal gesehen hat, bleibt hier liegen. Löscht jemand eine
Nachricht später, zeigt die Seite trotzdem, was drinstand. Außerdem: was du "für mich" gelöscht hast, bleibt ausgeblendet."""
import json
import logging
import sqlite3
import threading

from .config import get_settings

log = logging.getLogger("chats.store")
_db: sqlite3.Connection | None = None
_lock = threading.Lock()


def db() -> sqlite3.Connection:
    global _db
    if _db is None:
        d = get_settings().data_dir
        try:
            d.mkdir(parents=True, exist_ok=True)
            _db = sqlite3.connect(d / "chats.db", check_same_thread=False)
        except (OSError, sqlite3.Error) as e:   # kein Volume: läuft weiter, merkt sich aber nur bis zum Neustart
            log.warning("Kein Speicher unter %s (%s) – Nachrichten nur im Arbeitsspeicher", d, e)
            _db = sqlite3.connect(":memory:", check_same_thread=False)
        _db.execute("CREATE TABLE IF NOT EXISTS msgs (chat TEXT, id TEXT, at TEXT, text TEXT, files TEXT, hidden INTEGER DEFAULT 0, PRIMARY KEY (chat, id))")
        _db.commit()
    return _db


def remember(chat: str, items: list[dict]) -> None:
    rows = [(chat, str(m["id"]), m.get("at") or "", m.get("text") or "", json.dumps(m.get("files") or [], ensure_ascii=False))
            for m in items if m.get("id") and (m.get("text") or m.get("files"))]
    if rows:
        with _lock:
            db().executemany("INSERT INTO msgs (chat, id, at, text, files) VALUES (?,?,?,?,?) "
                             "ON CONFLICT (chat, id) DO UPDATE SET text=excluded.text, files=excluded.files", rows)
            db().commit()


def recall(chat: str, ids: list[str]) -> dict[str, dict]:
    """{Nachrichten-ID: {text, files, hidden}} für alles, was zu diesen IDs gemerkt ist."""
    if not ids:
        return {}
    with _lock:
        cur = db().execute(f"SELECT id, text, files, hidden FROM msgs WHERE chat=? AND id IN ({','.join('?' * len(ids))})", [chat, *map(str, ids)])
        return {i: {"text": t, "files": json.loads(f or "[]"), "hidden": bool(h)} for i, t, f, h in cur}


def hide(chat: str, msg_id: str) -> None:
    with _lock:
        db().execute("INSERT INTO msgs (chat, id, at, text, files, hidden) VALUES (?,?,'','','[]',1) "
                     "ON CONFLICT (chat, id) DO UPDATE SET hidden=1, text='', files='[]'", (chat, str(msg_id)))
        db().commit()
