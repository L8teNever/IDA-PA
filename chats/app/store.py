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


# ---------------- Zusammengeführte Chats (nur in IDA Chats; Beeper bleibt unberührt) ----------------
def _merges() -> sqlite3.Connection:
    d = db()
    d.execute("CREATE TABLE IF NOT EXISTS merges (chat TEXT PRIMARY KEY, grp TEXT, title TEXT, network TEXT, pos INTEGER)")
    return d


def groups() -> dict[str, list[dict]]:
    """{Gruppen-ID (= erster Chat): [{id, title, network}, …]}"""
    with _lock:
        rows = _merges().execute("SELECT grp, chat, title, network FROM merges ORDER BY grp, pos").fetchall()
    out: dict[str, list[dict]] = {}
    for g, c, t, n in rows:
        out.setdefault(g, []).append({"id": c, "title": t or "", "network": n or ""})
    return {g: m for g, m in out.items() if len(m) > 1}


def group_of(chat: str) -> str | None:
    with _lock:
        r = _merges().execute("SELECT grp FROM merges WHERE chat=?", (chat,)).fetchone()
    return r[0] if r else None


def merge(chats: list[dict]) -> str:
    """Chats (und ihre bisherigen Gruppen) zu einer Gruppe zusammenlegen. Die Gruppe heißt nach dem ersten Chat."""
    with _lock:
        d = _merges()
        members: list[dict] = []
        for c in chats:
            g = d.execute("SELECT grp FROM merges WHERE chat=?", (c["id"],)).fetchone()
            olds = [{"id": i, "title": t, "network": n} for i, t, n in
                    d.execute("SELECT chat, title, network FROM merges WHERE grp=? ORDER BY pos", (g[0],))] if g else [c]
            for o in olds:
                if o["id"] == c["id"]:
                    o = {**o, **{k: v for k, v in c.items() if v}}
                if o["id"] not in {m["id"] for m in members}:
                    members.append(o)
        grp = members[0]["id"]
        d.executemany("INSERT INTO merges (chat, grp, title, network, pos) VALUES (?,?,?,?,?) ON CONFLICT (chat) DO UPDATE SET "
                      "grp=excluded.grp, title=excluded.title, network=excluded.network, pos=excluded.pos",
                      [(m["id"], grp, m.get("title") or "", m.get("network") or "", i) for i, m in enumerate(members)])
        d.commit()
    return grp


def split(grp: str, chat: str | None = None) -> None:
    """Ganze Gruppe auflösen oder nur einen Chat herausnehmen."""
    with _lock:
        d = _merges()
        if chat and chat != grp:
            d.execute("DELETE FROM merges WHERE grp=? AND chat=?", (grp, chat))
        else:
            d.execute("DELETE FROM merges WHERE grp=?", (grp,))
        if d.execute("SELECT COUNT(*) FROM merges WHERE grp=?", (grp,)).fetchone()[0] < 2:
            d.execute("DELETE FROM merges WHERE grp=?", (grp,))
        d.commit()
