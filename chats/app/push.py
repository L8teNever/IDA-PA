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

from fastapi import APIRouter, HTTPException, Request
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
    d.execute("CREATE TABLE IF NOT EXISTS fcm_tokens (token TEXT PRIMARY KEY, at REAL)")
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
        return bool(_db().execute("SELECT 1 FROM push_subs LIMIT 1").fetchone() or _db().execute("SELECT 1 FROM fcm_tokens LIMIT 1").fetchone())


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


# ---------------- Einstellungen (für alle Geräte gleich) ----------------
# snaps_quiet: Snapchat-Snaps zählen nicht als neue Nachricht (keine Meldung, Chat rutscht nicht nach oben)
# snap_join: Snapchat-Videostücke desselben Absenders zeigt die Oberfläche als ein Video
PREFS = {"snaps_quiet": False, "snap_join": True}


def prefs() -> dict[str, bool]:
    with store._lock:
        got = dict(_db().execute(f"SELECT k, v FROM kv WHERE k IN ({','.join('?' * len(PREFS))})", [f"pref_{k}" for k in PREFS]).fetchall())
    return {k: got.get(f"pref_{k}", "1" if v else "0") == "1" for k, v in PREFS.items()}


def snaps_quiet() -> bool:
    return prefs()["snaps_quiet"]


def set_prefs(new: dict[str, bool]) -> dict[str, bool]:
    with store._lock:
        _db().executemany("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", [(f"pref_{k}", "1" if v else "0") for k, v in new.items() if k in PREFS])
        _db().commit()
    return prefs()


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
    """Schickt eine Meldung an alle angemeldeten Geräte (Browser per Web Push, Android-App per FCM); abgelaufene Abos fliegen raus.
    → Anzahl zugestellt."""
    return await _send_web(payload) + await fcm_send(payload)


async def _send_web(payload: dict) -> int:
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


# ---------------- Android-App: Firebase Cloud Messaging (FCM) ----------------
# Die Android-App (android/, docs/CHATS-ANDROID.md) kann kein Web Push. Sie bekommt Meldungen über Googles FCM – der einzige Weg, der
# das Handy schlafen lässt. Das Firebase-Projekt gehört dem Nutzer; seine beiden Dateien liegen NUR hier (chats.db), nie im Repo und
# nie in der APK: google-services.json (daraus holt sich die App nach der Anmeldung ihre Verbindungsdaten) und der Schlüssel des
# Dienstkontos (damit darf dieser Dienst senden – verlässt den Server nie).
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_fcm_tok: tuple[float, str] | None = None   # (gültig bis, Zugriffstoken)


def _kv(k: str) -> str:
    with store._lock:
        r = _db().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else ""


def _kv_set(k: str, v: str) -> None:
    with store._lock:
        if v:
            _db().execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, v))
        else:
            _db().execute("DELETE FROM kv WHERE k=?", (k,))
        _db().commit()


def fcm_client() -> dict | None:
    """Verbindungsdaten für die App (aus google-services.json) – oder None, solange nichts hochgeladen ist."""
    try:
        return json.loads(_kv("fcm_client")) or None
    except ValueError:
        return None


def fcm_account() -> dict | None:
    try:
        return json.loads(_kv("fcm_account")) or None
    except ValueError:
        return None


def fcm_tokens() -> list[str]:
    with store._lock:
        return [t for (t,) in _db().execute("SELECT token FROM fcm_tokens")]


def parse_services(data: dict, package: str = "") -> dict:
    """google-services.json → das, was die App zum Einrichten braucht. Nimmt die App mit passendem Paketnamen, sonst die erste."""
    try:
        info = data["project_info"]
        clients = data["client"]
        c = next((x for x in clients if x["client_info"]["android_client_info"]["package_name"] == package), clients[0])
        out = {"project_id": str(info["project_id"]), "sender_id": str(info["project_number"]),
               "app_id": str(c["client_info"]["mobilesdk_app_id"]), "api_key": str(c["api_key"][0]["current_key"]),
               "package": str(c["client_info"]["android_client_info"]["package_name"])}
    except (KeyError, IndexError, TypeError) as e:
        raise HTTPException(400, "Das ist keine google-services.json aus Firebase") from e
    if not all(out.values()):
        raise HTTPException(400, "In der google-services.json fehlen Angaben")
    return out


def check_account(data: dict) -> dict:
    if data.get("type") != "service_account" or not all(isinstance(data.get(k), str) and data.get(k) for k in ("project_id", "private_key", "client_email")):
        raise HTTPException(400, "Das ist keine Schlüssel-Datei eines Dienstkontos (Firebase → Projekteinstellungen → Dienstkonten)")
    try:
        from cryptography.hazmat.primitives import serialization
        serialization.load_pem_private_key(data["private_key"].encode(), None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "Der private Schlüssel in der Datei ist nicht lesbar") from e
    return {k: data[k] for k in ("project_id", "private_key", "client_email")} | {"token_uri": data.get("token_uri") or "https://oauth2.googleapis.com/token"}


def _jwt(acc: dict) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"iss": acc["client_email"], "scope": FCM_SCOPE, "aud": acc["token_uri"], "iat": now, "exp": now + 3600}).encode())
    key = serialization.load_pem_private_key(acc["private_key"].encode(), None)
    return f"{head}.{body}.{_b64(key.sign(f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"


async def _fcm_access(acc: dict, c) -> str:
    """Zugriffstoken von Google (eine Stunde gültig, hier 50 Minuten gemerkt)."""
    global _fcm_tok
    if _fcm_tok and _fcm_tok[0] > time.time():
        return _fcm_tok[1]
    r = await c.post(acc["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": _jwt(acc)})
    if r.status_code != 200:
        raise RuntimeError(f"Google lehnt das Dienstkonto ab (HTTP {r.status_code}): {r.text[:200]}")
    _fcm_tok = (time.time() + 3000, r.json()["access_token"])
    return _fcm_tok[1]


def _fcm_key(k: str) -> str:
    """Reservierte data-Schlüssel umbenennen (aus "from" wird "sender"), sonst weist FCM die ganze Meldung ab."""
    if k == "from":
        return "sender"
    return f"x_{k}" if k in ("notification", "message_type") or k.startswith(("google", "gcm")) else k


async def fcm_send(payload: dict, tokens: list[str] | None = None) -> int:
    """Meldung an die Android-Geräte. Nur Daten (kein „notification“-Block): die App baut die Meldung selbst, auch wenn sie zu ist.
    Abgemeldete Geräte (404 / UNREGISTERED) fliegen raus. → Anzahl zugestellt."""
    import httpx
    acc, toks = fcm_account(), fcm_tokens() if tokens is None else tokens
    if not acc or not toks:
        return 0
    # FCM erlaubt in data nur Text und lehnt reservierte Schlüssel mit 400 ab ("from", "notification", "message_type", "google…", "gcm…")
    data = {_fcm_key(k): str(v) for k, v in payload.items() if v is not None}
    ok, gone = 0, []
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            access = await _fcm_access(acc, c)
            url = f"https://fcm.googleapis.com/v1/projects/{acc['project_id']}/messages:send"

            async def one(t: str) -> int:
                r = await c.post(url, headers={"authorization": f"Bearer {access}"},
                                 json={"message": {"token": t, "data": data, "android": {"priority": "HIGH", "ttl": "3600s"}}})
                if r.status_code not in (200, 404):
                    log.warning("FCM-Antwort %s: %s", r.status_code, r.text[:300])
                return r.status_code
            for t, code in zip(toks, await asyncio.gather(*(one(t) for t in toks), return_exceptions=True)):
                if code == 200:
                    ok += 1
                elif code == 404:   # UNREGISTERED: die App ist deinstalliert oder hat ein neues Token
                    gone.append(t)
                elif isinstance(code, int) and code in (401, 403):
                    global _fcm_tok
                    _fcm_tok = None
                    log.warning("FCM lehnt ab (HTTP %s) – Dienstkonto und Projekt prüfen", code)
                elif not isinstance(code, int) or code >= 400:
                    log.warning("FCM: %s", code)
    except Exception as e:  # noqa: BLE001 – Android darf die Browser-Meldungen nicht aufhalten
        log.warning("FCM fehlgeschlagen: %s", e)
        if tokens is not None:
            raise
    if gone:
        with store._lock:
            _db().executemany("DELETE FROM fcm_tokens WHERE token=?", [(t,) for t in gone])
            _db().commit()
    return ok


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


class Prefs(BaseModel):
    snaps_quiet: bool | None = None
    snap_join: bool | None = None


@router.get("/prefs")
async def get_prefs():
    return prefs()


@router.put("/prefs")
async def put_prefs(body: Prefs):
    return set_prefs({k: v for k, v in body.model_dump().items() if v is not None})


class FcmToken(BaseModel):
    token: str = Field(min_length=20, max_length=4096)


MAX_JSON = 20_000


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_JSON:
        raise HTTPException(413, "Datei zu groß")
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise HTTPException(400, "Das ist keine JSON-Datei") from e
    if not isinstance(d, dict):
        raise HTTPException(400, "Das ist keine JSON-Datei")
    return d


@router.get("/fcm")
async def fcm_status():
    """Stand der Android-Benachrichtigungen – ohne Geheimnisse."""
    c, a = fcm_client(), fcm_account()
    return {"client": bool(c), "account": bool(a), "project": (c or {}).get("project_id", ""), "account_project": (a or {}).get("project_id", ""),
            "package": (c or {}).get("package", ""), "devices": len(fcm_tokens())}


@router.put("/fcm/client")
async def fcm_put_client(request: Request):
    """google-services.json aus Firebase hochladen (kommt nicht ins Repo und nicht in die APK)."""
    c = parse_services(await _json_body(request), get_settings().android_package)
    _kv_set("fcm_client", json.dumps(c))
    return await fcm_status()


@router.put("/fcm/account")
async def fcm_put_account(request: Request):
    """Schlüssel-Datei des Dienstkontos hochladen – bleibt auf dem Server, wird nie wieder ausgegeben."""
    global _fcm_tok
    _kv_set("fcm_account", json.dumps(check_account(await _json_body(request))))
    _fcm_tok = None
    return await fcm_status()


@router.delete("/fcm")
async def fcm_delete():
    """Firebase-Dateien und alle angemeldeten Android-Geräte vergessen."""
    global _fcm_tok
    _kv_set("fcm_client", "")
    _kv_set("fcm_account", "")
    _fcm_tok = None
    with store._lock:
        _db().execute("DELETE FROM fcm_tokens")
        _db().commit()
    return await fcm_status()


@router.get("/fcm/client")
async def fcm_get_client():
    """Verbindungsdaten für die Android-App (holt sie sich nach der Anmeldung, statt sie fest eingebaut zu haben)."""
    c = fcm_client()
    if not c:
        raise HTTPException(404, "Firebase ist noch nicht eingerichtet (Einstellungen → Android-App)")
    return c


@router.post("/fcm/register")
async def fcm_register(body: FcmToken):
    with store._lock:
        _db().execute("INSERT OR REPLACE INTO fcm_tokens (token, at) VALUES (?, ?)", (body.token, time.time()))
        _db().commit()
    return {"ok": True}


@router.post("/fcm/unregister")
async def fcm_unregister(body: FcmToken):
    with store._lock:
        _db().execute("DELETE FROM fcm_tokens WHERE token=?", (body.token,))
        _db().commit()
    return {"ok": True}


@router.post("/fcm/test")
async def fcm_test(body: FcmToken):
    """Probemeldung an genau dieses Gerät – zeigt, ob Firebase-Projekt, Dienstkonto und App zusammenpassen."""
    if not fcm_account():
        raise HTTPException(400, "Der Schlüssel des Dienstkontos fehlt noch")
    try:
        n = await fcm_send({"chat": "", "title": "Chats", "body": "So sieht eine Benachrichtigung aus.", "test": "1"}, [body.token])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Senden ging nicht: {e}") from e
    if not n:
        raise HTTPException(502, "Google hat die Meldung nicht angenommen – passen google-services.json und Dienstkonto zum selben Projekt?")
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
