"""Login for the web UI: one password, a signed session cookie.

The password comes from APP_PASSWORD; without it one is generated at the first start and printed to the
log once. Only a scrypt hash is stored. Internal routes (/mcp/{token}) authenticate with their own token.
"""
import hashlib
import hmac
import logging
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings
from .db import session_scope
from .services import settings_store

log = logging.getLogger("ida.auth")
COOKIE = "ida_session"
# reachable without a session
PUBLIC_PREFIXES = ("/login", "/api/v1/auth/", "/api/v1/health", "/static/", "/mcp/", "/favicon")

_signer: TimestampSigner | None = None
_password_hash: str | None = None


def _hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def _check(password: str, stored: str) -> bool:
    salt, _, h = stored.partition("$")
    return hmac.compare_digest(_hash(password, bytes.fromhex(salt)).split("$")[1], h)


async def init_auth() -> None:
    """Load (or create) the secret key and the password hash."""
    global _signer, _password_hash
    s = get_settings()
    async with session_scope() as db:
        key = s.secret_key or await settings_store.get(db, "secret_key")
        if not key:
            key = secrets.token_urlsafe(32)
            await settings_store.put(db, "secret_key", key)
        if s.app_password:
            stored = await settings_store.get(db, "password_hash")
            if not stored or not _check(s.app_password, stored):
                stored = _hash(s.app_password)
                await settings_store.put(db, "password_hash", stored)
        else:
            stored = await settings_store.get(db, "password_hash")
            if not stored:
                pw = secrets.token_urlsafe(12)
                stored = _hash(pw)
                await settings_store.put(db, "password_hash", stored)
                log.warning("=" * 60)
                log.warning("Kein APP_PASSWORD gesetzt. Erzeugtes Passwort: %s", pw)
                log.warning("Setze APP_PASSWORD in der .env, um es zu ändern.")
                log.warning("=" * 60)
    _signer = TimestampSigner(key, salt="ida-session")
    _password_hash = stored


def verify_password(password: str) -> bool:
    return bool(_password_hash) and _check(password, _password_hash)


def make_cookie() -> str:
    assert _signer
    return _signer.sign(secrets.token_hex(8)).decode()


def valid_cookie(value: str | None) -> bool:
    if not value or not _signer:
        return False
    try:
        _signer.unsign(value, max_age=get_settings().session_days * 86400)
        return True
    except (BadSignature, SignatureExpired):
        return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(PUBLIC_PREFIXES) or valid_cookie(request.cookies.get(COOKIE)):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
        nxt = path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse("/login" + ("" if path == "/" else f"?next={nxt}"), status_code=303)
