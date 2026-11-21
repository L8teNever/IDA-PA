"""Wer darf? Oberfläche und API: Cloudflare Access (JWT geprüft, wenn ACCESS_TEAM + ACCESS_AUD gesetzt) oder IDA mit Bearer-Token.
Der Agent-Pfad /api/v1/agent/ws hat eigene Anmeldung (Geräte-Secret / Einmal-Code), siehe main.py.
"""
import logging
import secrets
import time
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import HTTPException, Request, WebSocket

from .config import get_settings

log = logging.getLogger("health.auth")

_jwks: dict = {"at": 0.0, "client": None, "team": ""}


def _jwk_client():
    s = get_settings()
    if not _jwks["client"] or _jwks["team"] != s.access_team or time.time() - _jwks["at"] > 3600:
        _jwks["team"] = s.access_team
        _jwks["client"] = jwt.PyJWKClient(f"https://{s.access_team}.cloudflareaccess.com/cdn-cgi/access/certs", cache_keys=True)
        _jwks["at"] = time.time()
    return _jwks["client"]


def verify_access_jwt(token: str) -> str | None:
    s = get_settings()
    try:
        key = _jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=s.access_aud,
                            issuer=f"https://{s.access_team}.cloudflareaccess.com")
        return str(claims.get("email") or claims.get("common_name") or "access")
    except (jwt.PyJWTError, httpx.HTTPError, OSError) as e:
        log.info("Access-JWT abgelehnt: %s", e)
        return None


def who(headers, cookies) -> str | None:
    """Name des Aufrufers oder None (= nicht erlaubt)."""
    s = get_settings()
    authz = headers.get("authorization", "")
    if s.ida_token and authz.startswith("Bearer ") and secrets.compare_digest(authz[7:].strip(), s.ida_token):
        return "ida"
    from .settings_ui import admin_session  # Einrichtungs-Code (Notfall-Zugang, siehe settings_ui.py)
    if admin_session(cookies):
        return "einrichtung"
    tok = headers.get("cf-access-jwt-assertion") or cookies.get("CF_Authorization")
    if s.access_team and s.access_aud:
        return verify_access_jwt(tok) if tok else None
    if s.ida_token:
        # ohne JWT-Prüfung: nur durch, wenn Cloudflare Access davor stand (Header kommt vom Tunnel)
        return headers.get("cf-access-authenticated-user-email") or ("access" if tok else None)
    # gar nicht abgesichert: zu (Health gibt Root-Shells auf allen Geräten) – außer ausdrücklich zum Testen
    return "lokal" if s.allow_open else None


def secured() -> bool:
    s = get_settings()
    return bool(s.ida_token or (s.access_team and s.access_aud) or s.allow_open)


def require(request: Request) -> str:
    w = who(request.headers, request.cookies)
    if w:
        return w
    if not secured():
        raise HTTPException(503, "Health ist noch nicht abgesichert – im Browser mit dem Einrichtungs-Code anmelden (steht im Log des Dienstes health) und unter Einstellungen Cloudflare Access eintragen.")
    raise HTTPException(401, "Nicht angemeldet")


def _origin_ok(origin: str | None, host: str) -> bool:
    """Origin (falls gesendet) = diese Seite: Host-Kopfzeile oder die öffentliche Adresse (falls der Tunnel Host umschreibt)."""
    if not origin:
        return True
    o = origin.split("://", 1)[-1].rstrip("/").lower()
    ok = {host.lower()}
    pub = get_settings().public_url
    if pub:
        ok.add(urlsplit(pub).netloc.lower())
    return o in ok


def require_ws(ws: WebSocket) -> str | None:
    """Für Browser-WebSockets zusätzlich: Origin muss diese Seite sein (gegen Cross-Site-WebSocket-Hijacking)."""
    w = who(ws.headers, ws.cookies)
    if not w:
        return None
    if w != "ida" and not _origin_ok(ws.headers.get("origin"), ws.headers.get("host", "")):
        return None
    return w


def same_origin(request: Request) -> bool:
    """POST aus dem Browser: Origin (falls gesendet) muss diese Seite sein."""
    return _origin_ok(request.headers.get("origin"), request.headers.get("host", ""))
