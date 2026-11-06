"""MCP-Connectoren mit OAuth: automatische Erkennung, Login im Browser (PKCE, Registrierung), Token erneuern,
Client-ID/Secret ohne Login – gegen einen kleinen Ersatz-Server, der MCP und OAuth zugleich spielt."""
import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

API = "/api/v1"


class FakeOAuthMcp:
    def __init__(self, dcr: bool = True, auth_methods=("client_secret_post", "client_secret_basic", "none")):
        self.tokens: dict[str, float] = {}       # access token -> gültig bis
        self.refresh: dict[str, str] = {}        # refresh token -> client
        self.codes: dict[str, dict] = {}         # code -> {challenge, client, redirect}
        self.clients: dict[str, str] = {"fest-id": "fest-secret"}
        self.token_requests: list[dict] = []
        self.ttl = 3600
        outer = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, obj=None, headers=None):
                raw = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if obj is not None:
                    self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("content-length", 0) or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                base = outer.url
                u = urlparse(self.path)
                if u.path == "/.well-known/oauth-protected-resource/mcp":
                    return self._send(200, {"resource": f"{base}/mcp", "authorization_servers": [base]})
                if u.path == "/.well-known/oauth-authorization-server":
                    meta = {"issuer": base, "authorization_endpoint": f"{base}/authorize", "token_endpoint": f"{base}/token",
                            "token_endpoint_auth_methods_supported": list(auth_methods), "code_challenge_methods_supported": ["S256"],
                            "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"], "scopes_supported": ["mcp"]}
                    if dcr:
                        meta["registration_endpoint"] = f"{base}/register"
                    return self._send(200, meta)
                if u.path == "/authorize":  # „Login“: gibt sofort einen Code zurück (der Test folgt der Weiterleitung selbst)
                    q = {k: v[0] for k, v in parse_qs(u.query).items()}
                    code = f"code{len(outer.codes)}"
                    outer.codes[code] = {"challenge": q["code_challenge"], "client": q["client_id"], "redirect": q["redirect_uri"], "resource": q.get("resource")}
                    return self._send(302, None, {"location": f"{q['redirect_uri']}?code={code}&state={q['state']}"})
                return self._send(404, {})

            def do_POST(self):
                u = urlparse(self.path)
                raw = self._body()
                if u.path == "/register":
                    b = json.loads(raw)
                    cid = f"dyn{len(outer.clients)}"
                    outer.clients[cid] = ""
                    return self._send(201, {"client_id": cid, "redirect_uris": b["redirect_uris"]})
                if u.path == "/token":
                    f = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    auth = self.headers.get("authorization", "")
                    if auth.startswith("Basic "):
                        cid, sec = base64.b64decode(auth[6:]).decode().split(":", 1)
                        f.update(client_id=cid, client_secret=sec, _basic="1")
                    outer.token_requests.append(f)
                    g = f.get("grant_type")
                    if g == "authorization_code":
                        c = outer.codes.pop(f.get("code"), None)
                        ok = c and base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=").decode() == c["challenge"]
                        if not ok or c["redirect"] != f.get("redirect_uri"):
                            return self._send(400, {"error": "invalid_grant"})
                    elif g == "refresh_token":
                        if f.get("refresh_token") not in outer.refresh:
                            return self._send(400, {"error": "invalid_grant"})
                        outer.refresh.pop(f["refresh_token"])  # Refresh-Token wird bei jedem Erneuern ausgetauscht
                    elif g == "client_credentials":
                        if outer.clients.get(f.get("client_id")) != f.get("client_secret") or not f.get("client_secret"):
                            return self._send(401, {"error": "invalid_client"})
                    else:
                        return self._send(400, {"error": "unsupported_grant_type"})
                    tok = f"tok{len(outer.tokens)}"
                    outer.tokens[tok] = time.time() + outer.ttl
                    out = {"access_token": tok, "token_type": "Bearer", "expires_in": outer.ttl}
                    if g != "client_credentials":
                        rt = f"rt{len(outer.tokens)}"
                        outer.refresh[rt] = f.get("client_id", "")
                        out["refresh_token"] = rt
                    return self._send(200, out)
                if u.path == "/mcp":
                    tok = self.headers.get("authorization", "")[7:]
                    if outer.tokens.get(tok, 0) < time.time():
                        return self._send(401, {"error": "unauthorized"},
                                          {"www-authenticate": f'Bearer resource_metadata="{outer.url}/.well-known/oauth-protected-resource/mcp"'})
                    m = json.loads(raw)
                    if "id" not in m:
                        return self._send(202, None)
                    res = {"initialize": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}},
                           "tools/list": {"tools": [{"name": "chats_suchen", "description": "Chats durchsuchen", "inputSchema": {"type": "object"}}]}}
                    return self._send(200, {"jsonrpc": "2.0", "id": m["id"], "result": res.get(m["method"], {})})
                return self._send(404, {})

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def fake():
    f = FakeOAuthMcp()
    yield f
    f.server.shutdown()


def _login(client, cid: str) -> str:
    """Spielt den Browser: Login-Adresse holen, Anbieter „besuchen“, Rücksprung zu IDA folgen."""
    import httpx
    start = client.post(f"{API}/connectors/{cid}/oauth/start").json()["url"]
    back = httpx.get(start, follow_redirects=False).headers["location"]
    path = back[back.index("/api/v1/"):]
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def test_browser_login_with_automatic_registration_and_refresh(client, fake):
    m = client.post(f"{API}/connectors", json={"name": "Beeper Test", "url": f"{fake.url}/mcp", "agents": []}).json()
    assert m["status"] == "login" and m["oauth"]["grant"] == "code" and not m["oauth"]["logged_in"]
    start = client.post(f"{API}/connectors/{m['id']}/oauth/start").json()["url"]
    assert "code_challenge_method=S256" in start and "client_id=dyn" in start and "resource=" in start and "scope=mcp" in start
    assert _login(client, m["id"]).endswith(f"/einstellungen/mcp/{m['id']}?oauth=ok")
    got = client.get(f"{API}/connectors/{m['id']}").json()
    assert got["status"] == "ok" and got["tools"] == ["chats_suchen"] and got["oauth"]["logged_in"] and got["oauth"]["registered"]
    assert "access_token" not in json.dumps(got) and "tok" not in json.dumps(got["oauth"])  # Tokens nie an den Browser
    # Token abgelaufen: beim nächsten Aufruf erneuert Ida es mit dem Refresh-Token (das dabei ausgetauscht wird)
    fake.tokens.clear()
    r = client.post(f"{API}/connectors/{m['id']}/refresh").json()
    assert r["status"] == "ok"
    assert fake.token_requests[-1]["grant_type"] == "refresh_token"
    fake.tokens.clear()
    assert client.post(f"{API}/connectors/{m['id']}/refresh").json()["status"] == "ok"  # mit dem neuen Refresh-Token
    out = client.post(f"{API}/connectors/{m['id']}/oauth/logout").json()
    assert out["status"] == "login" and not out["oauth"]["logged_in"]
    client.delete(f"{API}/connectors/{m['id']}")


def test_client_id_and_secret_without_login(client, fake):
    m = client.post(f"{API}/connectors", json={"url": f"{fake.url}/mcp", "agents": [],
                                               "oauth": {"grant": "client_credentials", "client_id": "fest-id", "client_secret": "fest-secret"}}).json()
    assert m["status"] == "ok" and m["tools"] == ["chats_suchen"]
    assert m["oauth"]["has_secret"] and "fest-secret" not in json.dumps(m)
    assert fake.token_requests[-1]["grant_type"] == "client_credentials"
    # falsches Secret: klare Meldung, kein Absturz; leeres Secret beim Ändern behält das gespeicherte
    bad = client.put(f"{API}/connectors/{m['id']}/oauth", json={"grant": "client_credentials", "client_id": "fest-id", "client_secret": "falsch"}).json()
    assert bad["status"] == "error" and "Token abgelehnt" in bad["status_text"]
    ok = client.put(f"{API}/connectors/{m['id']}/oauth", json={"grant": "client_credentials", "client_id": "fest-id", "client_secret": "fest-secret"}).json()
    assert ok["status"] == "ok"
    keep = client.put(f"{API}/connectors/{m['id']}/oauth", json={"grant": "client_credentials", "client_id": "fest-id", "client_secret": None, "scope": "mcp"}).json()
    assert keep["status"] == "ok" and keep["oauth"]["scope"] == "mcp"
    assert client.post(f"{API}/connectors", json={"url": f"{fake.url}/mcp", "oauth": {"grant": "client_credentials", "client_id": "x"}}).status_code == 400
    client.delete(f"{API}/connectors/{m['id']}")


def test_fixed_client_id_with_secret_and_basic_auth(client):
    f = FakeOAuthMcp(dcr=False, auth_methods=("client_secret_basic",))
    try:
        m = client.post(f"{API}/connectors", json={"url": f"{f.url}/mcp", "agents": [],
                                                   "oauth": {"grant": "code", "client_id": "fest-id", "client_secret": "fest-secret"}}).json()
        assert m["status"] == "login"
        _login(client, m["id"])
        assert f.token_requests[-1]["_basic"] == "1" and f.token_requests[-1]["client_id"] == "fest-id"  # Server kann nur Basic
        assert client.get(f"{API}/connectors/{m['id']}").json()["status"] == "ok"
        client.delete(f"{API}/connectors/{m['id']}")
        # ohne Registrierung und ohne Client-ID: verständlicher Fehler statt Login ins Leere
        m2 = client.post(f"{API}/connectors", json={"url": f"{f.url}/mcp", "agents": []}).json()
        r = client.post(f"{API}/connectors/{m2['id']}/oauth/start")
        assert r.status_code == 400 and "Client-ID" in r.json()["detail"]
        client.delete(f"{API}/connectors/{m2['id']}")
    finally:
        f.server.shutdown()


def test_callback_with_unknown_state_or_error(client, fake):
    r = client.get(f"{API}/connectors/oauth/callback?state=gibtsnicht&code=x", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/einstellungen/mcp?oauth=")
    m = client.post(f"{API}/connectors", json={"url": f"{fake.url}/mcp", "agents": []}).json()
    start = client.post(f"{API}/connectors/{m['id']}/oauth/start").json()["url"]
    state = dict(x.split("=", 1) for x in start.split("?", 1)[1].split("&"))["state"]
    r = client.get(f"{API}/connectors/oauth/callback?state={state}&error=access_denied", follow_redirects=False)
    assert "oauth=fehler" in r.headers["location"] and "access_denied" in r.headers["location"]
    client.delete(f"{API}/connectors/{m['id']}")


def test_plain_server_without_oauth_stays_as_before(client):
    """Ein Server ohne 401 bekommt kein OAuth untergeschoben (Erkennung schlägt still fehl)."""
    m = client.post(f"{API}/connectors", json={"url": "http://127.0.0.1:9/mcp", "agents": []}).json()
    assert m["oauth"] is None and m["status"] == "error"
    client.delete(f"{API}/connectors/{m['id']}")
