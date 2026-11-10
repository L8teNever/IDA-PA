"""Einstellungen → Google: reicht an den Google-Dienst durch (hier mit einem Ersatz-Dienst per MockTransport)."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.api import google as g
from app.services.errors import Invalid


def _setup(monkeypatch, handler, url="http://google:4571"):
    monkeypatch.setattr(g, "get_settings", lambda: SimpleNamespace(google_api_url=url, google_api_token="tok"))
    monkeypatch.setattr(g, "_client", lambda: httpx.AsyncClient(base_url="http://google:4571", transport=httpx.MockTransport(handler)))


def test_nicht_eingerichtet(monkeypatch):
    monkeypatch.setattr(g, "get_settings", lambda: SimpleNamespace(google_api_url="", google_api_token=""))
    assert asyncio.run(g.status()) == {"configured": False}
    with pytest.raises(Invalid):
        asyncio.run(g.login())


def test_status_und_login(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, req.content))
        if req.url.path.endswith("/auth/status"):
            return httpx.Response(200, json={"verbunden": True, "konto": "a@b.de", "dienste": []})
        return httpx.Response(200, json={"url": "https://accounts.google.com/o/oauth2/v2/auth?x=1"})

    _setup(monkeypatch, handler)
    assert asyncio.run(g.status())["konto"] == "a@b.de"
    assert asyncio.run(g.login())["url"].startswith("https://accounts.google.com/")
    assert seen[1][0:2] == ("POST", "/api/v1/auth/start")


def test_rueckweg_von_google(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.url.path, req.content))
        if b"schlecht" in req.content:
            return httpx.Response(400, json={"detail": "Ungueltiger oder abgelaufener Anmelde-Versuch"})
        return httpx.Response(200, json={"ok": True})

    _setup(monkeypatch, handler)
    ok = asyncio.run(g.callback(code="c1", state="s1"))
    assert ok.headers["location"] == "/einstellungen/google?google=ok"
    assert seen[0][0] == "/api/v1/auth/callback" and b'"code"' in seen[0][1] and b"s1" in seen[0][1]
    bad = asyncio.run(g.callback(code="c1", state="schlecht"))
    assert bad.headers["location"].startswith("/einstellungen/google?google=fehler&grund=")
    nope = asyncio.run(g.callback(code="", state="", error="access_denied"))
    assert nope.headers["location"].endswith("grund=access_denied")


def test_fehler_und_nicht_erreichbar(monkeypatch):
    _setup(monkeypatch, lambda req: httpx.Response(400, json={"detail": "kaputt"}))
    with pytest.raises(Invalid, match="kaputt"):
        asyncio.run(g.login())

    def down(req):
        raise httpx.ConnectError("weg")

    _setup(monkeypatch, down)
    with pytest.raises(Invalid, match="nicht erreichbar"):
        asyncio.run(g.status())
