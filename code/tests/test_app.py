"""IDA Code: Weiterleitung an IDA (nur freigegebene Endpunkte, Streaming) und Auslieferung der Oberfläche."""
import json

import httpx
import pytest

from app.main import allowed, create_app


def _resp(status: int, data, ctype: str = "application/json") -> httpx.Response:
    # wie ein echter Server: der Inhalt kommt als Stream (sonst kann der Proxy ihn nicht roh weiterreichen)
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return httpx.Response(status, headers={"content-type": ctype}, stream=httpx.ByteStream(raw))


def fake_ida(seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/api/v1/chat":
            body = json.loads(req.content)
            lines = [{"chat": {"id": "c1"}}, {"text": body["text"].upper()}, {"done": {"ok": True}}]
            return _resp(200, "".join(json.dumps(x) + "\n" for x in lines).encode(), "application/x-ndjson")
        if req.url.path == "/api/v1/code/projects/missing":
            return _resp(404, {"detail": "Projekt nicht gefunden"})
        return _resp(200, {"path": req.url.path, "query": dict(req.url.params), "method": req.method})
    return handler


@pytest.fixture
async def client():
    seen: list = []
    app = create_app()
    app.state.client = httpx.AsyncClient(base_url="http://ida", transport=httpx.MockTransport(fake_ida(seen)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://code") as c:
        c.seen = seen
        yield c
    await app.state.client.aclose()


def test_allowed_paths():
    for p in ("bootstrap", "usage", "chat", "chats/abc", "chats/abc/live", "chats/abc/stop", "code/projects", "code/projects/x/raw/a/b.html",
              "github/status", "github", "uploads", "uploads/f1", "files/f1"):
        assert allowed(p), p
    for p in ("agents", "accounts/a1/terminal", "chats", "settings", "routines", "chats/a/b/c", "memory", "../agents",
              "code/../agents", "code/./../accounts", "github/..\\agents"):
        assert not allowed(p), p


async def test_health(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


async def test_proxy_get_with_query(client):
    r = await client.get("/api/v1/github/repos", params={"q": "ida"})
    assert r.status_code == 200
    assert r.json() == {"path": "/api/v1/github/repos", "query": {"q": "ida"}, "method": "GET"}


async def test_proxy_passes_errors(client):
    r = await client.get("/api/v1/code/projects/missing")
    assert r.status_code == 404 and r.json()["detail"] == "Projekt nicht gefunden"


async def test_proxy_streams_chat(client):
    r = await client.post("/api/v1/chat", json={"text": "hallo"})
    assert r.status_code == 200
    events = [json.loads(x) for x in r.text.splitlines() if x]
    assert events == [{"chat": {"id": "c1"}}, {"text": "HALLO"}, {"done": {"ok": True}}]
    assert client.seen[-1].method == "POST"


async def test_blocked_paths_never_reach_ida(client):
    for p in ("/api/v1/agents", "/api/v1/chats", "/api/v1/accounts/a1/terminal"):
        r = await client.get(p)
        assert r.status_code == 404
    assert client.seen == []


async def test_frontend_served(client):
    r = await client.get("/p/abc/t/def")
    assert r.status_code == 200 and "IDA Code" in r.text
    assert '"/static/' not in r.text and '"/s/' in r.text
    sw = await client.get("/sw.js")
    assert sw.status_code == 200 and "__VERSION__" not in sw.text and "__SHELL__" not in sw.text
    m = await client.get("/manifest.webmanifest")
    assert m.json()["name"] == "IDA Code"
    assert (await client.get("/api/v1/unbekannt")).status_code == 404
