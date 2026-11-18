"""Später senden: Nachrichten, die der Dienst zur gewählten Zeit abschickt. Beeper selbst bietet das in seiner API nicht an – darum liegen
sie hier (SQLite, chats.db) und ein Takt im Hintergrund schickt sie ab, auch wenn die App zu ist.
Auf Wunsch „nur senden, wenn ich bis dahin nichts geschrieben habe“: Hat der Nutzer in der Zwischenzeit selbst etwas in den Chat geschickt,
wird die geplante Nachricht nicht mehr gesendet (Status skipped) und bleibt sichtbar, bis er sie verwirft oder neu plant."""
import asyncio
import json
import logging
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import api, bus, store

log = logging.getLogger("chats.schedule")
router = APIRouter(prefix="/api/v1")

TICK_S = 10            # so oft schaut der Takt nach Fälligem
GRACE_S = 30 * 60      # später als das nach der Zeit: nicht mehr senden (Dienst war aus) – „verpasst“
RETRY_S = 30           # ging das Senden schief: nach so vielen Sekunden noch einmal
MAX_TRIES = 3
KEEP_S = 3 * 86400     # nicht Gesendetes (verpasst, übersprungen, fehlgeschlagen) bleibt so lange sichtbar
MAX_PENDING = 50
OWN_SENT_WINDOW_S = 180
_busy: set[str] = set()   # gerade in Arbeit (Takt und „Jetzt senden“ nicht doppelt)

COLS = ("id", "chat", "target", "text", "reply_to", "mentions", "due", "created", "silent", "status", "note", "tries", "done")


def _tbl():
    d = store.db()
    d.execute("""CREATE TABLE IF NOT EXISTS scheduled (id TEXT PRIMARY KEY, chat TEXT NOT NULL, target TEXT NOT NULL, text TEXT NOT NULL,
        reply_to TEXT DEFAULT '', mentions TEXT DEFAULT '[]', due REAL NOT NULL, created REAL NOT NULL, silent INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending', note TEXT DEFAULT '', tries INTEGER DEFAULT 0, done REAL DEFAULT 0)""")
    return d


def _rows(where: str = "", args: tuple = ()) -> list[dict]:
    with store._lock:
        cur = _tbl().execute(f"SELECT {', '.join(COLS)} FROM scheduled {where} ORDER BY due", args)
        return [dict(zip(COLS, r)) for r in cur.fetchall()]


def _get(sid: str) -> dict:
    r = _rows("WHERE id = ?", (sid,))
    if not r:
        raise HTTPException(404, "Geplante Nachricht nicht gefunden")
    return r[0]


def _set(sid: str, **kw) -> None:
    with store._lock:
        d = _tbl()
        d.execute(f"UPDATE scheduled SET {', '.join(f'{k} = ?' for k in kw)} WHERE id = ?", (*kw.values(), sid))
        d.commit()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pub(r: dict) -> dict:
    return {"id": r["id"], "chat": r["chat"], "target": r["target"], "text": r["text"], "reply_to": r["reply_to"] or None,
            "mentions": json.loads(r["mentions"] or "[]"), "at": _iso(r["due"]), "created": _iso(r["created"]), "silent": bool(r["silent"]),
            "status": r["status"], "note": r["note"]}


def _changed(chat: str) -> None:
    bus.publish({"type": "sched", "id": chat})   # offene Fenster laden die Liste neu


def parse_at(s: str) -> float:
    try:
        d = datetime.fromisoformat(s.strip())
    except ValueError as e:
        raise HTTPException(400, "Ungültige Zeit") from e
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def check_when(ts: float) -> None:
    now = time.time()
    if ts < now + 5:
        raise HTTPException(400, "Die Zeit liegt schon in der Vergangenheit")
    if ts > now + 366 * 86400:
        raise HTTPException(400, "Höchstens ein Jahr im Voraus")


# ---------------- Routen ----------------
class ScheduleIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    at: str = Field(max_length=40)                                   # ISO-Zeit (mit Zeitzone)
    target: str = Field(default="", max_length=300)                  # bei zusammengeführten Chats: der Chat, über den gesendet wird
    reply_to: str | None = Field(default=None, max_length=300)
    silent: bool = False                                             # nur senden, wenn ich bis dahin nichts geschrieben habe
    mentions: list[api.Mention] = Field(default_factory=list, max_length=50)


class ScheduleEdit(BaseModel):
    at: str | None = Field(default=None, max_length=40)
    silent: bool | None = None
    text: str | None = Field(default=None, min_length=1, max_length=8000)


@router.post("/chats/{chat_id}/schedule")
async def create(chat_id: str, body: ScheduleIn):
    api.check_ids(chat_id)
    if chat_id == "ida" or chat_id.startswith("untis:"):
        raise HTTPException(400, "Für diesen Chat geht „Später senden“ nicht")
    members = api.group_members(chat_id)
    target = body.target or (members[0] if members else chat_id)
    api.check_ids(target)
    if (members and target not in members) or (not members and target != chat_id) or target == "ida" or target.startswith("untis:"):
        raise HTTPException(400, "Ungültiger Chat zum Senden")
    if body.reply_to:
        api.check_ids(body.reply_to)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Leere Nachricht")
    due = parse_at(body.at)
    check_when(due)
    if len(_rows("WHERE status = 'pending'")) >= MAX_PENDING:
        raise HTTPException(400, f"Höchstens {MAX_PENDING} geplante Nachrichten")
    sid = secrets.token_hex(6)
    with store._lock:
        d = _tbl()
        d.execute("INSERT INTO scheduled (id, chat, target, text, reply_to, mentions, due, created, silent) VALUES (?,?,?,?,?,?,?,?,?)",
                  (sid, chat_id, target, text, body.reply_to or "", json.dumps([m.model_dump() for m in body.mentions if m.name and f"@{m.name}" in text]),
                   due, time.time(), 1 if body.silent else 0))
        d.commit()
    _changed(chat_id)
    return pub(_get(sid))


@router.get("/scheduled")
async def list_scheduled():
    """Alles, was noch aussteht oder nicht gesendet wurde (verpasst, übersprungen, fehlgeschlagen). Gesendetes sind jetzt normale Nachrichten."""
    return {"items": [pub(r) for r in _rows("WHERE status != 'sent'")]}


@router.patch("/scheduled/{sid}")
async def edit(sid: str, body: ScheduleEdit):
    api.check_ids(sid)
    r = _get(sid)
    if r["status"] == "sent":
        raise HTTPException(409, "Schon gesendet")
    kw: dict = {}
    if body.at is not None:
        ts = parse_at(body.at)
        check_when(ts)
        kw.update(due=ts, status="pending", note="", tries=0, created=time.time() if r["status"] != "pending" else r["created"])
    elif r["status"] != "pending":
        raise HTTPException(400, "Bitte eine neue Zeit wählen")
    if body.silent is not None:
        kw["silent"] = 1 if body.silent else 0
    if body.text is not None:
        t = body.text.strip()
        if not t:
            raise HTTPException(400, "Leere Nachricht")
        kw["text"] = t
    if kw:
        _set(sid, **kw)
    _changed(r["chat"])
    return pub(_get(sid))


@router.post("/scheduled/{sid}/send")
async def send_now(sid: str):
    api.check_ids(sid)
    r = _get(sid)
    if r["status"] == "sent":
        raise HTTPException(409, "Schon gesendet")
    if sid in _busy:
        raise HTTPException(409, "Wird gerade gesendet")
    _busy.add(sid)
    try:
        await deliver(r)
        _set(sid, status="sent", note="", done=time.time())
    finally:
        _busy.discard(sid)
    _changed(r["chat"])
    return {"ok": True}


@router.delete("/scheduled/{sid}")
async def remove(sid: str):
    api.check_ids(sid)
    r = _get(sid)
    with store._lock:
        d = _tbl()
        d.execute("DELETE FROM scheduled WHERE id = ?", (sid,))
        d.commit()
    _changed(r["chat"])
    return {"ok": True}


# ---------------- Senden und der Takt ----------------
async def deliver(r: dict) -> None:
    """Über denselben Weg wie jede Nachricht aus der App."""
    mentions = [api.Mention(**m) for m in json.loads(r["mentions"] or "[]")]
    await api.send(r["target"], api.Send(text=r["text"], reply_to=r["reply_to"] or None, mentions=mentions))


def _ts(s) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


async def wrote_meanwhile(r: dict) -> bool:
    """Hat der Nutzer seit dem Planen selbst etwas in diesen Chat geschickt (bei zusammengeführten Chats: in einen der Chats)?
    Reaktionen und Hinweise zählen nicht, ebenso nicht, was dieser Takt für ihn schon aus Geplantem gesendet hat. Kann man es nicht prüfen,
    kommt ein Fehler (dann wird später noch einmal versucht) – lieber nicht senden als zu viel."""
    chats = [c for c in (api.group_members(r["chat"]) or [r["target"]]) if c != "ida" and not c.startswith("untis:")]
    ours = _rows("WHERE chat = ? AND status = 'sent' AND done >= ?", (r["chat"], r["created"] - 5))
    for c in chats:
        d = await api.fetch_messages(c, "", "before")
        for m in d.get("items", []):
            if not m.get("isSender") or (m.get("type") or "").upper() in ("REACTION", "NOTICE") or not (m.get("text") or m.get("attachments")):
                continue
            ts = _ts(m.get("timestamp"))
            if ts <= r["created"] + 3:
                continue
            txt = (m.get("text") or "").strip()
            if any(o["text"].strip() == txt and abs(o["done"] - ts) <= OWN_SENT_WINDOW_S for o in ours):
                continue
            return True
    return False


def _mark(r: dict, status: str, note: str = "") -> None:
    _set(r["id"], status=status, note=note, done=time.time())
    _changed(r["chat"])


async def run_one(r: dict, now: float) -> None:
    if r["id"] in _busy:
        return
    _busy.add(r["id"])
    try:
        if now - r["due"] > GRACE_S:
            return _mark(r, "missed", "Zur geplanten Zeit lief der Dienst nicht – nicht gesendet")
        try:
            if r["silent"] and await wrote_meanwhile(r):
                return _mark(r, "skipped", "Du hast in der Zwischenzeit selbst geschrieben – nicht gesendet")
            await deliver(r)
        except HTTPException as e:
            return _retry(r, now, str(e.detail))
        except Exception as e:  # noqa: BLE001 – ein Fehler hier darf den Takt nie anhalten
            log.exception("Später senden: Senden von %s fehlgeschlagen", r["id"])
            return _retry(r, now, str(e))
        _mark(r, "sent")
    finally:
        _busy.discard(r["id"])


def _retry(r: dict, now: float, why: str) -> None:
    tries = r["tries"] + 1
    if tries >= MAX_TRIES:
        log.warning("Später senden: %s nach %d Versuchen aufgegeben: %s", r["id"], tries, why)
        _set(r["id"], tries=tries)
        return _mark(r, "failed", f"Senden ging nicht: {why[:160]}")
    _set(r["id"], tries=tries, due=now + RETRY_S)


async def run_due(now: float | None = None) -> None:
    now = time.time() if now is None else now
    for r in _rows("WHERE status = 'pending' AND due <= ?", (now,)):
        await run_one(r, now)


def purge(now: float | None = None) -> None:
    now = time.time() if now is None else now
    with store._lock:
        d = _tbl()
        d.execute("DELETE FROM scheduled WHERE status != 'pending' AND done > 0 AND done < ?", (now - KEEP_S,))
        d.commit()


async def loop() -> None:
    last_clean = 0.0
    while True:
        try:
            await run_due()
            if time.time() - last_clean > 3600:
                last_clean = time.time()
                purge()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Später senden: Durchlauf fehlgeschlagen")
        await asyncio.sleep(TICK_S)
