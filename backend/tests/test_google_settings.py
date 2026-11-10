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
        asyncio.run(g.login(g.LoginIn(return_to="x")))


def test_status_und_login(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, req.content))
        if req.url.path.endswith("/auth/status"):
            return httpx.Response(200, json={"verbunden": True, "konto": "a@b.de", "dienste": []})
        return httpx.Response(200, json={"url": "https://google.example/authorize?ticket=t"})

    _setup(monkeypatch, handler)
    assert asyncio.run(g.status())["konto"] == "a@b.de"
    assert asyncio.run(g.login(g.LoginIn(return_to="https://ida.example/einstellungen/google")))["url"].endswith("ticket=t")
    assert seen[1][0:2] == ("POST", "/api/v1/auth/start") and b"einstellungen/google" in seen[1][2]


def test_fehler_und_nicht_erreichbar(monkeypatch):
    _setup(monkeypatch, lambda req: httpx.Response(400, json={"detail": "Rueckkehr-Adresse nicht erlaubt"}))
    with pytest.raises(Invalid, match="nicht erlaubt"):
        asyncio.run(g.login(g.LoginIn(return_to="https://boese.example")))

    def down(req):
        raise httpx.ConnectError("weg")

    _setup(monkeypatch, down)
    with pytest.raises(Invalid, match="nicht erreichbar"):
        asyncio.run(g.status())
