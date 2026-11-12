"""Web Push for your messages (messages app): every message to "user" goes to the subscribed devices as a notification.

Built on `cryptography` only (no pywebpush): VAPID (RFC 8292) + aes128gcm payload encryption (RFC 8291). The VAPID key is
created on first use and kept in the settings table. Sending happens after the commit, in the background, so the code that
writes the message never waits for the push services.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import USER, Agent, Message, PushSubscription
from . import settings_store

log = logging.getLogger("ida.push")
KEY = "push_vapid"
SUBJECT = "https://ida.kulbarts.com"  # VAPID contact (a URL is allowed; no e-mail address sent to the push services)
TTL = 24 * 3600  # a phone that is off gets it within a day, otherwise it is dropped
MAX_BODY = 1500  # push payloads are limited to ~4 KB after encryption


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _raw_public(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------------- VAPID key ----------------
_vapid: ec.EllipticCurvePrivateKey | None = None


async def vapid_key(db) -> ec.EllipticCurvePrivateKey:
    global _vapid
    if _vapid is None:
        stored = await settings_store.get(db, KEY)
        if stored and stored.get("private"):
            _vapid = ec.derive_private_key(int.from_bytes(unb64u(stored["private"]), "big"), ec.SECP256R1())
        else:
            k = ec.generate_private_key(ec.SECP256R1())
            await settings_store.put(db, KEY, {"private": b64u(k.private_numbers().private_value.to_bytes(32, "big")), "public": b64u(_raw_public(k))})
            await db.flush()
            _vapid = k
    return _vapid


async def public_key(db) -> str:
    return b64u(_raw_public(await vapid_key(db)))


def vapid_header(key: ec.EllipticCurvePrivateKey, endpoint: str) -> str:
    u = urlsplit(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": SUBJECT}, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={b64u(_raw_public(key))}"


# ---------------- payload encryption (RFC 8291, aes128gcm) ----------------
def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    ua_public = unb64u(p256dh)
    as_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(as_key)
    shared = as_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(unb64u(auth), shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02 = last (and only) record
    return salt + struct.pack(">IB", 4096, len(as_public)) + as_public + body


# ---------------- sending ----------------
async def send_to(sub: PushSubscription, data: dict, key: ec.EllipticCurvePrivateKey, client: httpx.AsyncClient) -> int:
    body = encrypt(json.dumps(data, ensure_ascii=False).encode(), sub.p256dh, sub.auth)
    # Urgency high: delivered right away even when the phone sleeps (Doze) – these are few, deliberate messages
    r = await client.post(sub.endpoint, content=body, headers={
        "authorization": vapid_header(key, sub.endpoint), "content-encoding": "aes128gcm", "content-type": "application/octet-stream",
        "ttl": str(TTL), "urgency": "high", "topic": str(data.get("id", ""))[:32] or "ida"})
    return r.status_code


def payload(m: Message, sender: str) -> dict:
    text = m.text if len(m.text) <= MAX_BODY else m.text[:MAX_BODY - 1] + "…"
    return {"id": m.id, "title": m.title or sender, "from": sender, "body": text, "priority": m.priority, "at": m.at.isoformat() if m.at else None}


async def _send_all(db, datas: list[dict]) -> dict:
    """Sends the payloads to every device; devices the push service no longer knows (404/410) are removed."""
    subs = (await db.scalars(select(PushSubscription))).all()
    if not subs or not datas:
        return {"sent": 0, "failed": 0, "devices": len(subs)}
    key = await vapid_key(db)
    sent = failed = 0
    gone: set[str] = set()
    async with httpx.AsyncClient(timeout=15) as client:
        for data in datas:
            for sub in subs:
                if sub.id in gone:
                    continue
                try:
                    code = await send_to(sub, data, key, client)
                except Exception as e:  # noqa: BLE001 – one device must not stop the others
                    log.warning("Push an %s fehlgeschlagen: %s", sub.device[:40] or sub.id, e)
                    failed += 1
                    continue
                if code in (404, 410):
                    gone.add(sub.id)
                elif code < 300:
                    sub.last_ok = utcnow()
                    sent += 1
                else:
                    log.warning("Push an %s: HTTP %s", sub.device[:40] or sub.id, code)
                    failed += 1
    if gone:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
    return {"sent": sent, "failed": failed, "devices": len(subs) - len(gone), "removed": len(gone)}


async def deliver(ids: list[str]) -> dict:
    async with session_scope() as db:
        if not await db.scalar(select(PushSubscription.id).limit(1)):
            return {"sent": 0, "failed": 0, "devices": 0}
        msgs = (await db.scalars(select(Message).where(Message.id.in_(ids)).order_by(Message.at))).all()
        names = {a.id: a.name for a in (await db.scalars(select(Agent))).all()}
        return await _send_all(db, [payload(m, names.get(m.from_id, "IDA" if m.from_id == USER else m.from_id)) for m in msgs])


async def test(db) -> dict:
    """A test notification to every device (not stored as a message)."""
    return await _send_all(db, [{"id": "test", "title": "Test", "from": "IDA", "body": "Push klappt. So sehen Idas Nachrichten auf diesem Gerät aus.",
                                 "priority": "normal", "at": utcnow().isoformat()}])


# ---------------- hook: every committed message to "user" ----------------
_tasks: set[asyncio.Task] = set()


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _ctx) -> None:
    for o in session.new:
        if isinstance(o, Message) and o.to_id == USER:
            session.info.setdefault("push_ids", []).append(o.id)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    ids = session.info.pop("push_ids", None)
    if not ids or not get_settings().push_enabled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    t = loop.create_task(_deliver_quietly(ids))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop("push_ids", None)


async def _deliver_quietly(ids: list[str]) -> None:
    try:
        await deliver(ids)
    except Exception:  # noqa: BLE001
        log.exception("Push fehlgeschlagen")
