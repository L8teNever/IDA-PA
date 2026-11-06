"""OAuth 2.1 für MCP-Connectoren (MCP-Spezifikation „Authorization“): anmelden, Token speichern, automatisch erneuern.

Zwei Arten (Connector.oauth["grant"]):
  code                – Login im Browser (Authorization Code + PKCE S256). Ohne Client-ID registriert sich IDA selbst
                        (Dynamic Client Registration), wenn der Server das anbietet.
  client_credentials  – ohne Login, nur mit Client-ID und Client-Secret (Maschine zu Maschine).

Gefunden wird der Anmelde-Server wie in der Spezifikation: 401 mit WWW-Authenticate resource_metadata=… bzw.
/.well-known/oauth-protected-resource, dann /.well-known/oauth-authorization-server (oder openid-configuration);
ganz ohne Metadaten gelten /authorize, /token, /register auf dem Host des MCP-Servers.

Alles liegt in Connector.oauth (JSON). Tokens und Secret gehen nie an den Browser (siehe serializers.connector).
"""
import asyncio
import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import inspect

from ...db import session_scope
from ...models import Connector

CALLBACK = "/api/v1/connectors/oauth/callback"
PENDING_S = 900  # so lange gilt ein angefangener Login
EARLY_S = 60     # so viel früher als nötig wird erneuert
_locks: dict[str, asyncio.Lock] = {}


class OAuthError(Exception):
    pass


def active(c: Connector) -> bool:
    return bool((c.oauth or {}).get("grant"))


def logged_in(c: Connector) -> bool:
    return bool((c.oauth or {}).get("access_token"))


def public_view(c: Connector) -> dict | None:
    """Was die Oberfläche sehen darf – nie Tokens oder das Secret."""
    o = c.oauth or {}
    if not o.get("grant"):
        return None
    return {"grant": o["grant"], "client_id": o.get("client_id") or "", "registered": bool(o.get("registered")),
            "has_secret": bool(o.get("client_secret")), "scope": o.get("scope") or "", "logged_in": bool(o.get("access_token")),
            "expires_at": o.get("expires_at"), "refreshable": bool(o.get("refresh_token") or o["grant"] == "client_credentials"),
            "issuer": (o.get("meta") or {}).get("issuer") or ""}


# ---------------- Discovery ----------------
def _origin(url: str) -> str:
    u = urlsplit(url)
    return urlunsplit((u.scheme, u.netloc, "", "", ""))


def _well_known(base: str, name: str) -> list[str]:
    """RFC 8414/9728: /.well-known/<name><pfad>, dann ohne Pfad."""
    u = urlsplit(base)
    path = u.path.rstrip("/")
    out = [urlunsplit((u.scheme, u.netloc, f"/.well-known/{name}{path}", "", ""))] if path else []
    return out + [urlunsplit((u.scheme, u.netloc, f"/.well-known/{name}", "", ""))]


async def _json(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        r = await client.get(url, headers={"accept": "application/json", "MCP-Protocol-Version": "2025-06-18"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


async def discover(url: str, client: httpx.AsyncClient | None = None) -> dict:
    """→ {issuer, authorization_endpoint, token_endpoint, registration_endpoint?, …, resource}. Wirft OAuthError, wenn
    der Server gar nicht nach OAuth aussieht (kein 401)."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=True)
    try:
        prm_urls = []
        try:
            r = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ida", "version": "1"}}},
                                  headers={"accept": "application/json, text/event-stream"})
            m = re.search(r'resource_metadata="([^"]+)"', r.headers.get("www-authenticate", ""))
            if m:
                prm_urls.append(m.group(1))
            if r.status_code not in (401, 403) and not m:
                raise OAuthError(f"Der Server verlangt keine Anmeldung (HTTP {r.status_code})")
        except httpx.HTTPError as e:
            raise OAuthError("Server nicht erreichbar") from e
        prm = None
        for u in prm_urls + _well_known(url, "oauth-protected-resource"):
            prm = await _json(client, u)
            if prm:
                break
        auth_base = ((prm or {}).get("authorization_servers") or [None])[0] or _origin(url)
        meta = None
        for u in _well_known(auth_base, "oauth-authorization-server") + _well_known(auth_base, "openid-configuration"):
            meta = await _json(client, u)
            if meta and meta.get("token_endpoint"):
                break
            meta = None
        if not meta:  # alte Spezifikation (2025-03-26): feste Pfade auf dem Host
            o = _origin(auth_base)
            meta = {"issuer": o, "authorization_endpoint": f"{o}/authorize", "token_endpoint": f"{o}/token", "registration_endpoint": f"{o}/register",
                    "guessed": True}
        keep = ("issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint", "scopes_supported", "grant_types_supported",
                "token_endpoint_auth_methods_supported", "code_challenge_methods_supported", "guessed")
        out = {k: meta[k] for k in keep if k in meta}
        out["resource"] = (prm or {}).get("resource") or url
        if prm and prm.get("scopes_supported") and not out.get("scopes_supported"):
            out["scopes_supported"] = prm["scopes_supported"]
        return out
    finally:
        if own:
            await client.aclose()


# ---------------- Tokens ----------------
async def _save(c: Connector, oauth: dict) -> None:
    """Hängt der Connector an einer offenen Sitzung, speichert die mit (eine zweite Sitzung auf dieselbe Zeile würde unter
    PostgreSQL auf die erste warten, die wiederum hierauf wartet). Sonst eine eigene, kurze Sitzung – wichtig, weil manche
    Server das Refresh-Token bei jedem Erneuern austauschen."""
    c.oauth = oauth  # neues Objekt: SQLAlchemy merkt die Änderung
    insp = inspect(c, raiseerr=False)
    if insp is not None and insp.session is not None and not insp.detached:
        return
    async with session_scope() as db:
        row = await db.get(Connector, c.id)
        if row is not None:
            row.oauth = dict(oauth)


def _client_auth(o: dict, body: dict) -> dict:
    """Client-Authentifizierung am Token-Endpunkt: Basic, wenn der Server nur das kann, sonst im Formular."""
    headers = {"accept": "application/json"}
    cid, secret = o.get("client_id") or "", o.get("client_secret") or ""
    methods = (o.get("meta") or {}).get("token_endpoint_auth_methods_supported") or []
    if secret and methods and "client_secret_post" not in methods and "client_secret_basic" in methods:
        raw = f"{cid}:{secret}".encode()
        headers["authorization"] = "Basic " + base64.b64encode(raw).decode()
    else:
        body["client_id"] = cid
        if secret:
            body["client_secret"] = secret
    return headers


async def _token(o: dict, body: dict) -> dict:
    meta = o.get("meta") or {}
    if not meta.get("token_endpoint"):
        raise OAuthError("Kein Token-Endpunkt bekannt")
    if meta.get("resource"):
        body.setdefault("resource", meta["resource"])  # RFC 8707 – Pflicht in der MCP-Spezifikation
    headers = _client_auth(o, body)
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(meta["token_endpoint"], data=body, headers=headers)
        except httpx.HTTPError as e:
            raise OAuthError("Anmelde-Server nicht erreichbar") from e
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code >= 400 or not d.get("access_token"):
        msg = d.get("error_description") or d.get("error") or f"HTTP {r.status_code}"
        raise OAuthError(f"Token abgelehnt: {msg}")
    return d


def _store(o: dict, tok: dict) -> dict:
    o = {**o, "access_token": tok["access_token"]}
    if tok.get("refresh_token"):
        o["refresh_token"] = tok["refresh_token"]
    exp = tok.get("expires_in")
    o["expires_at"] = int(time.time() + int(exp)) if isinstance(exp, (int, float)) or str(exp or "").isdigit() else None
    if tok.get("scope"):
        o["granted_scope"] = tok["scope"]
    o.pop("pending", None)
    o.pop("error", None)
    return o


def _with_meta(o: dict, meta: dict) -> dict:
    return {**o, "meta": meta}


async def _ensure_meta(c: Connector, o: dict) -> dict:
    if (o.get("meta") or {}).get("token_endpoint"):
        return o
    return _with_meta(o, await discover(c.url))


async def client_credentials(c: Connector) -> None:
    o = await _ensure_meta(c, dict(c.oauth or {}))
    if not o.get("client_id") or not o.get("client_secret"):
        raise OAuthError("Für „ohne Login“ braucht es Client-ID und Client-Secret")
    body = {"grant_type": "client_credentials"}
    if o.get("scope"):
        body["scope"] = o["scope"]
    await _save(c, _store(o, await _token(o, body)))


async def _refresh(c: Connector) -> None:
    o = dict(c.oauth or {})
    if o.get("grant") == "client_credentials":
        return await client_credentials(c)
    if not o.get("refresh_token"):
        raise OAuthError("Anmeldung abgelaufen – bitte neu anmelden")
    try:
        tok = await _token(o, {"grant_type": "refresh_token", "refresh_token": o["refresh_token"]})
    except OAuthError:
        await _save(c, {k: v for k, v in o.items() if k not in ("access_token", "refresh_token", "expires_at")})
        raise OAuthError("Anmeldung abgelaufen – bitte neu anmelden")
    await _save(c, _store(o, tok))


async def auth_headers(c: Connector, force_refresh: bool = False) -> dict:
    """Die Header für einen Aufruf: feste Header des Connectors + aktuelles Bearer-Token (bei Bedarf vorher erneuert)."""
    h = dict(c.headers or {})
    if not active(c):
        return h
    lock = _locks.setdefault(c.id, asyncio.Lock())
    async with lock:
        o = c.oauth or {}
        exp = o.get("expires_at")
        stale = not o.get("access_token") or (exp and exp - EARLY_S < time.time())
        if force_refresh or (stale and (o.get("refresh_token") or o.get("grant") == "client_credentials")):
            await _refresh(c)
        o = c.oauth or {}
    if o.get("access_token"):
        h["Authorization"] = f"Bearer {o['access_token']}"
    return h


# ---------------- Login im Browser ----------------
def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _register(meta: dict, redirect_uri: str) -> dict:
    """Dynamic Client Registration (RFC 7591): IDA meldet sich selbst als Client an."""
    if not meta.get("registration_endpoint"):
        raise OAuthError("Der Server kann keine automatische Registrierung – trag Client-ID (und ggf. Secret) ein")
    body = {"client_name": "IDA", "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none"}
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(meta["registration_endpoint"], json=body, headers={"accept": "application/json"})
        except httpx.HTTPError as e:
            raise OAuthError("Registrierung nicht erreichbar") from e
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code >= 400 or not d.get("client_id"):
        raise OAuthError(f"Registrierung abgelehnt: {d.get('error_description') or d.get('error') or r.status_code}")
    return d


async def start(c: Connector, base_url: str) -> str:
    """Bereitet den Login vor und gibt die Adresse zurück, zu der der Browser muss."""
    o = dict(c.oauth or {})
    if o.get("grant") != "code":
        raise OAuthError("Dieser Connector meldet sich ohne Browser an")
    o = await _ensure_meta(c, o)
    meta = o["meta"]
    redirect_uri = base_url.rstrip("/") + CALLBACK
    if not o.get("client_id") or (o.get("registered") and o.get("redirect_uri") != redirect_uri):
        reg = await _register(meta, redirect_uri)
        o.update({"client_id": reg["client_id"], "client_secret": reg.get("client_secret") or "", "registered": True, "redirect_uri": redirect_uri})
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    o["pending"] = {"state": state, "verifier": verifier, "redirect_uri": redirect_uri, "at": int(time.time())}
    params = {"response_type": "code", "client_id": o["client_id"], "redirect_uri": redirect_uri, "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256", "resource": meta.get("resource") or c.url}
    scope = o.get("scope") or " ".join((meta.get("scopes_supported") or [])[:20])
    if scope:
        params["scope"] = scope
    await _save(c, o)
    ep = meta["authorization_endpoint"]
    return ep + ("&" if "?" in ep else "?") + urlencode(params)


async def finish(c: Connector, code: str) -> None:
    o = dict(c.oauth or {})
    p = o.get("pending") or {}
    if not p or time.time() - p.get("at", 0) > PENDING_S:
        raise OAuthError("Der Login ist abgelaufen – bitte nochmal anmelden")
    tok = await _token(o, {"grant_type": "authorization_code", "code": code, "redirect_uri": p["redirect_uri"], "code_verifier": p["verifier"]})
    await _save(c, _store(o, tok))


def matches_state(c: Connector, state: str) -> bool:
    p = (c.oauth or {}).get("pending") or {}
    return bool(state) and bool(p.get("state")) and secrets.compare_digest(p["state"], state)


async def forget(c: Connector) -> None:
    """Abmelden: Tokens weg, Einstellungen (Client-ID, Secret, Scope) bleiben."""
    await _save(c, {k: v for k, v in (c.oauth or {}).items() if k not in ("access_token", "refresh_token", "expires_at", "pending", "granted_scope")})
