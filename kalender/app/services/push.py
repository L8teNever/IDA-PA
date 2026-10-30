"""Web Push (Opt-in): Erinnerungen klingeln auch bei geschlossener App.

Die Schlüssel (VAPID) erzeugt der Dienst beim ersten Gebrauch selbst und legt sie in `settings` ab, genauso die Abos der Geräte.
Achtung (siehe docs/KALENDER.md §11): Web Push läuft immer über den Push-Dienst des Browsers (bei Chrome/Android Googles FCM);
der Inhalt ist Ende-zu-Ende verschlüsselt, verlässt aber den Server. Deshalb ist es nur an, wenn der Nutzer ein Gerät anmeldet.
"""
import asyncio
import base64
import json
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import session_scope
from ..errors import Invalid
from . import settings as cfg
from .timeutil import local_now

log = logging.getLogger("kalender.push")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


async def public_key(db: AsyncSession) -> str:
    pub = await cfg.get(db, "vapid_public")
    if pub:
        return pub
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pub = _b64(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    await cfg.put(db, "vapid_private", pem)
    await cfg.put(db, "vapid_public", pub)
    await db.flush()
    return pub


async def subs(db: AsyncSession) -> list[dict]:
    return list(await cfg.get(db, "push_subs") or [])


async def subscribe(db: AsyncSession, sub: dict, label: str = "") -> int:
    if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://") or not isinstance(sub.get("keys"), dict):
        raise Invalid("Ungültiges Push-Abo")
    lst = [s for s in await subs(db) if s.get("endpoint") != sub["endpoint"]]
    lst.append({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"].get("p256dh", ""), "auth": sub["keys"].get("auth", "")},
                "label": label[:80], "at": datetime.now().isoformat(timespec="seconds")})
    await cfg.put(db, "push_subs", lst)
    return len(lst)


async def unsubscribe(db: AsyncSession, endpoint: str) -> int:
    lst = [s for s in await subs(db) if s.get("endpoint") != endpoint]
    await cfg.put(db, "push_subs", lst)
    return len(lst)


def _quiet(text: str) -> bool:
    try:
        a, b = (text or "").replace("–", "-").split("-")
        a, b = [int(x[:2]) * 60 + int(x[3:5]) for x in (a.strip(), b.strip())]
    except (ValueError, IndexError):
        return False
    n = local_now()
    x = n.hour * 60 + n.minute
    return a <= x < b if a < b else (x >= a or x < b)


def _send_one(sub: dict, data: str, key_pem: str, claims: dict) -> int:
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]}, data=data,
                vapid_private_key=Vapid01.from_pem(key_pem.encode()), vapid_claims=dict(claims), ttl=3600, timeout=10)
        return 200
    except WebPushException as e:
        return getattr(e.response, "status_code", 0) or 0
    except Exception:  # noqa: BLE001 – ein kaputtes Gerät darf die anderen nicht aufhalten
        log.exception("Push fehlgeschlagen")
        return 0


async def send(payloads: list[dict], force: bool = False) -> int:
    """Schickt Meldungen an alle angemeldeten Geräte; abgelaufene Abos (404/410) fliegen raus. → Anzahl zugestellt."""
    async with session_scope() as db:
        lst = await subs(db)
        key = await cfg.get(db, "vapid_private")
        quiet = (await cfg.get_all(db)).get("quiet_hours") or ""
    if not lst or not key or (not force and _quiet(quiet)):
        return 0
    s = get_settings()
    claims = {"sub": s.kalender_public_url if s.kalender_public_url.startswith("https://") else "mailto:kalender@localhost"}
    ok, dead = 0, set()
    for p in payloads:
        data = json.dumps(p, ensure_ascii=False)
        for sub in lst:
            code = await asyncio.to_thread(_send_one, sub, data, key, claims)
            if code in (404, 410):
                dead.add(sub["endpoint"])
            elif 200 <= code < 300:
                ok += 1
    if dead:
        async with session_scope(write=True) as db:
            await cfg.put(db, "push_subs", [x for x in await subs(db) if x["endpoint"] not in dead])
    return ok
