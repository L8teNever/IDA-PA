"""Benachrichtigungen aufs Handy (Web Push) und Stummschalten pro Chat.

Die App selbst läuft dafür nicht: der Dienst fragt Beeper alle PUSH_POLL_S Sekunden und schickt bei neuen ungelesenen Nachrichten eine
Meldung über den Push-Dienst des Browsers (Android/Chrome: Googles FCM, Inhalt Ende-zu-Ende verschlüsselt). Das Handy wird nur geweckt,
wenn wirklich etwas kommt – kein Timer, keine offene Verbindung (Akku). Die Schlüssel (VAPID) erzeugt der Dienst selbst (chats.db).
Stumm: bis zu einem Zeitpunkt oder für immer (until = 0). In Beeper stummgeschaltete Chats bleiben auch hier still.
"""
import asyncio
import base64
import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import store
from .config import get_settings

log = logging.getLogger("chats.push")
router = APIRouter(prefix="/api/v1")


def _db():
    d = store.db()
    d.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    d.execute("CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, p256dh TEXT, auth TEXT, at REAL)")
    d.execute("CREATE TABLE IF NOT EXISTS mutes (chat TEXT PRIMARY KEY, until REAL)")
    d.execute("CREATE TABLE IF NOT EXISTS autoread (chat TEXT PRIMARY KEY, mode TEXT)")
    return d


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def keys() -> tuple[str, str]:
    """(öffentlicher Schlüssel für den Browser, privater Schlüssel als PEM) – beim ersten Gebrauch erzeugt."""
    with store._lock:
        d = _db()
        got = dict(d.execute("SELECT k, v FROM kv WHERE k IN ('vapid_public', 'vapid_private')").fetchall())
        if got.get("vapid_public") and got.get("vapid_private"):
            return got["vapid_public"], got["vapid_private"]
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        pub = _b64(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        d.executemany("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", [("vapid_public", pub), ("vapid_private", pem)])
        d.commit()
        return pub, pem


def subs() -> list[dict]:
    with store._lock:
        rows = _db().execute("SELECT endpoint, p256dh, auth FROM push_subs").fetchall()
    return [{"endpoint": e, "keys": {"p256dh": p, "auth": a}} for e, p, a in rows]


def has_subs() -> bool:
    with store._lock:
        return bool(_db().execute("SELECT 1 FROM push_subs LIMIT 1").fetchone())


def _drop(endpoints: set[str]) -> None:
    if endpoints:
        with store._lock:
            _db().executemany("DELETE FROM push_subs WHERE endpoint=?", [(e,) for e in endpoints])
            _db().commit()


# ---------------- Stummschalten ----------------
def mutes() -> dict[str, float]:
    """{Chat-ID: bis (Unix-Zeit, 0 = für immer)} – Abgelaufenes fliegt dabei raus."""
    now = time.time()
    with store._lock:
        d = _db()
        d.execute("DELETE FROM mutes WHERE until > 0 AND until <= ?", (now,))
        d.commit()
        return {c: u for c, u in d.execute("SELECT chat, until FROM mutes")}


def muted(chat: str, m: dict[str, float] | None = None) -> bool:
    m = mutes() if m is None else m
    grp = store.group_of(chat)
    return chat in m or bool(grp and grp in m)


def mute(chat: str, hours: float) -> float:
    until = time.time() + hours * 3600 if hours > 0 else 0
    with store._lock:
        _db().execute("INSERT OR REPLACE INTO mutes (chat, until) VALUES (?, ?)", (chat, until))
        _db().commit()
    return until


def unmute(chat: str) -> None:
    with store._lock:
        _db().execute("DELETE FROM mutes WHERE chat=?", (chat,))
        _db().commit()


# ---------------- Automatisch gelesen: "open" = beim Öffnen, "always" = sofort, ohne dass du den Chat öffnest ----------------
AUTOREAD = ("open", "always")


def autoreads() -> dict[str, str]:
    with store._lock:
        return {c: m for c, m in _db().execute("SELECT chat, mode FROM autoread")}


def autoread_of(chat: str, a: dict[str, str] | None = None) -> str:
    a = autoreads() if a is None else a
    grp = store.group_of(chat)
    return a.get(chat) or (a.get(grp, "") if grp else "")


def set_autoread(chat: str, mode: str) -> None:
    with store._lock:
        if mode in AUTOREAD:
            _db().execute("INSERT OR REPLACE INTO autoread (chat, mode) VALUES (?, ?)", (chat, mode))
        else:
            _db().execute("DELETE FROM autoread WHERE chat=?", (chat,))
        _db().commit()


# ---------------- Senden ----------------
def _send_one(sub: dict, data: str, pem: str, claims: dict) -> int:
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush
    try:
        # Urgency high: Android stellt sofort zu, auch im Ruhemodus (wie bei WhatsApp)
        webpush(subscription_info=sub, data=data, vapid_private_key=Vapid01.from_pem(pem.encode()), vapid_claims=dict(claims),
                ttl=3600, timeout=10, headers={"Urgency": "high"})
        return 200
    except WebPushException as e:
        return getattr(e.response, "status_code", 0) or 0
    except Exception:  # noqa: BLE001 – ein kaputtes Gerät darf die anderen nicht aufhalten
        log.exception("Push fehlgeschlagen")
        return 0


async def send(payload: dict) -> int:
    """Schickt eine Meldung an alle angemeldeten Geräte; abgelaufene Abos (404/410) fliegen raus. → Anzahl zugestellt."""
    lst = subs()
    if not lst:
        return 0
    _, pem = keys()
    url = get_settings().chats_public_url.rstrip("/")
    claims = {"sub": url if re.match(r"^https://[\w.-]+$", url) else "mailto:chats@localhost"}   # nur Adresse ohne Pfad gilt
    data = json.dumps(payload, ensure_ascii=False)
    codes = await asyncio.gather(*(asyncio.to_thread(_send_one, s, data, pem, claims) for s in lst))
    _drop({s["endpoint"] for s, c in zip(lst, codes) if c in (404, 410)})
    return sum(200 <= c < 300 for c in codes)


# ---------------- Schnittstelle ----------------
class Sub(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: dict[str, str]


class Unsub(BaseModel):
    endpoint: str = Field(max_length=2000)


class AutoRead(BaseModel):
    mode: str = Field(default="", max_length=10)   # "", "open" oder "always"


class Mute(BaseModel):
    hours: float = Field(ge=0, le=24 * 365)   # 0 = bis du die Stummschaltung aufhebst


def _check_id(chat_id: str) -> None:
    if not re.match(r"^[\w!:.@=+-]{1,300}$", chat_id):
        raise HTTPException(400, "Ungültige ID")


@router.get("/push")
async def push_key():
    return {"key": keys()[0]}


@router.post("/push/subscribe")
async def subscribe(body: Sub):
    if not body.endpoint.startswith("https://") or not body.keys.get("p256dh") or not body.keys.get("auth"):
        raise HTTPException(400, "Ungültiges Push-Abo")
    with store._lock:
        _db().execute("INSERT OR REPLACE INTO push_subs (endpoint, p256dh, auth, at) VALUES (?,?,?,?)",
                      (body.endpoint, body.keys["p256dh"][:200], body.keys["auth"][:100], time.time()))
        _db().commit()
    return {"ok": True}


@router.post("/push/unsubscribe")
async def unsubscribe(body: Unsub):
    _drop({body.endpoint})
    return {"ok": True}


@router.get("/mutes")
async def list_mutes():
    return {"items": [{"id": c, "until": u} for c, u in mutes().items()]}


@router.put("/chats/{chat_id}/mute")
async def set_mute(chat_id: str, body: Mute):
    _check_id(chat_id)
    return {"id": chat_id, "until": mute(chat_id, body.hours)}


@router.delete("/chats/{chat_id}/mute")
async def del_mute(chat_id: str):
    _check_id(chat_id)
    unmute(chat_id)
    return {"ok": True}


@router.get("/autoread")
async def list_autoread():
    return {"items": [{"id": c, "mode": m} for c, m in autoreads().items()]}


@router.put("/chats/{chat_id}/autoread")
async def put_autoread(chat_id: str, body: AutoRead):
    _check_id(chat_id)
    if body.mode and body.mode not in AUTOREAD:
        raise HTTPException(400, "Unbekannte Einstellung")
    set_autoread(chat_id, body.mode)
    return {"id": chat_id, "mode": body.mode}
