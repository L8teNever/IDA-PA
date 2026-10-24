"""Login for the web UI.

1. Behind Cloudflare Zero Trust (CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD set): every request through the tunnel
   carries a signed Access token (header Cf-Access-Jwt-Assertion / cookie CF_Authorization). If its signature,
   audience, issuer and expiry are valid, the request is let in – no second login.
2. Otherwise (e.g. direct access to the port): one password, a signed session cookie. The password comes from
   APP_PASSWORD; without it one is generated at the first start and printed to the log once (scrypt hash stored).
Internal routes (/mcp/{token}) authenticate with their own token.
"""
import asyncio
import hashlib
import hmac
import logging
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
import jwt
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


# ---------------- Cloudflare Access ----------------
_jwks: jwt.PyJWKClient | None = None


def access_enabled() -> bool:
    s = get_settings()
    return bool(s.cf_access_team_domain and s.cf_access_aud)


def verify_access_token(token: str, signing_key) -> dict:
    """Checks a Cloudflare Access JWT (RS256, audience, issuer, expiry). Raises jwt.PyJWTError if invalid."""
    s = get_settings()
    return jwt.decode(token, signing_key, algorithms=["RS256"], audience=s.cf_access_aud,
                      issuer=f"https://{s.cf_access_team_domain}", options={"require": ["exp", "iat", "aud", "iss"]})


def _check_access(token: str) -> bool:
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"https://{get_settings().cf_access_team_domain}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
    try:
        verify_access_token(token, _jwks.get_signing_key_from_jwt(token).key)
        return True
    except (jwt.PyJWTError, OSError) as e:
        log.info("Cloudflare-Access-Token abgelehnt: %s", e)
        return False


async def valid_access(request) -> bool:
    """request: an HTTP Request or a WebSocket (both have headers and cookies)."""
    if not access_enabled():
        return False
    token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization")
    if not token:
        return False
    return await asyncio.to_thread(_check_access, token)  # key download is blocking (cached afterwards)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/login" and await valid_access(request):  # already signed in via Cloudflare
            return RedirectResponse(request.query_params.get("next", "/") if request.query_params.get("next", "/").startswith("/") and not request.query_params.get("next", "").startswith("//") else "/", status_code=303)
        if path.startswith(PUBLIC_PREFIXES) or valid_cookie(request.cookies.get(COOKIE)) or await valid_access(request):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
        nxt = path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse("/login" + ("" if path == "/" else f"?next={nxt}"), status_code=303)


async def websocket_allowed(ws) -> bool:
    """WebSockets bypass the middleware: same login check, plus the page must come from this host (no CSRF)."""
    origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
    if origin and origin.split("://", 1)[-1] != host:
        return False
    return valid_cookie(ws.cookies.get(COOKIE)) or await valid_access(ws)
