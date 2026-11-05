"""Belegte Adressen im Wiki-Gedächtnis: Abgleich von Websites und Kurzlinks."""
import asyncio
import json

import httpx
import pytest

from app import wiki_memory
from app.config import get_settings
from app.tools import sites

from .test_server import server  # noqa: F401

FOLDER = wiki_memory.FOLDER


class FakeWiki:
    def __init__(self):
        self.pages: dict[str, dict] = {}
        self.saves: list[dict] = []
        self.deletes: list[str] = []
        self.headers = None
        self.down = False
        self.refuse = ""

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("weg")
        self.headers = req.headers
        p = req.url.path
        body = json.loads(req.content) if req.content else None
        if p == "/api/v1/pages" and req.method == "GET":
            return httpx.Response(200, json={"pages": [{"path": x} for x in self.pages]})
        if p == "/api/v1/page":
            return httpx.Response(200, json=self.pages[req.url.params["ref"]])
        if p == "/api/v1/pages":
            if self.refuse and self.refuse in body["path"]:
                return httpx.Response(400, json={"detail": "abgelehnt"})
            self.saves.append(body)
            self.pages[body["path"]] = body
            return httpx.Response(200, json={})
        if p == "/api/v1/pages/delete":
            self.deletes.append(body["ref"])
            del self.pages[body["ref"]]
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
async def wiki(server, monkeypatch):  # noqa: F811
    fake = FakeWiki()
    monkeypatch.setenv("WIKI_URL", "http://wiki:8000")
    monkeypatch.setenv("WIKI_IDA_TOKEN", "geheim")
    monkeypatch.setenv("LINKS_DOMAINS", "l.example.com,link.example.com")
    get_settings.cache_clear()
    real = httpx.AsyncClient
    monkeypatch.setattr(wiki_memory.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(fake), **kw))
    wiki_memory.reset()
    yield fake
    wiki_memory.reset()


def site(**kw):
    return {"name": "shop", "hostname": "app.example.com", "path": "", "source": "files", "repo": "", "zero_trust": False, "created": 1793800000, **kw}


async def settle():
    await asyncio.gather(*wiki_memory._tasks)


async def test_site_and_link_get_a_page(server, wiki):  # noqa: F811
    sites.save([site()])
    r = await server.post("/api/v1/links", json={"target": "https://ziel.example.net/a?token=geheim#x", "code": "lebenslauf", "note": "Bewerbung"})
    assert r.status_code == 200
    await settle()

    s = wiki.pages[f"{FOLDER}/website-app-example-com"]
    assert "https://app.example.com" in s["body"] and "Website „shop“" in s["body"] and "Erstellt" in s["body"] and "öffentlich" in s["body"]
    assert s["force_new"] and s["frontmatter"]["category"] == "projekte" and s["frontmatter"]["summary"]

    link = wiki.pages[f"{FOLDER}/link-lebenslauf"]
    assert "https://l.example.com/lebenslauf" in link["body"] and "https://link.example.com/lebenslauf" in link["body"] and "Bewerbung" in link["body"]
    assert "https://ziel.example.net/a" in link["body"] and "geheim" not in json.dumps(link)  # Parameter bleiben draußen

    assert "l.example.com, link.example.com" in wiki.pages[f"{FOLDER}/link-domains"]["body"]
    assert wiki.headers["x-wiki-by"] == "server" and wiki.headers["authorization"] == "Bearer geheim"


async def test_deleting_removes_the_page(server, wiki):  # noqa: F811
    sites.save([site()])
    d = (await server.post("/api/v1/links", json={"target": "https://a.net", "code": "weg-damit"})).json()
    await settle()
    assert (await server.delete(f"/api/v1/links/{d['id']}", headers={"content-type": "application/json"})).status_code == 200
    await settle()
    assert wiki.deletes == [f"{FOLDER}/link-weg-damit"]

    sites.save([])
    await wiki_memory.sync(server.app)
    assert f"{FOLDER}/website-app-example-com" in wiki.deletes and f"{FOLDER}/link-domains" in wiki.pages


async def test_changed_link_moves_its_page(server, wiki):  # noqa: F811
    d = (await server.post("/api/v1/links", json={"target": "https://a.net", "code": "alt-name"})).json()
    await settle()
    await server.put(f"/api/v1/links/{d['id']}", json={"target": "https://b.net", "code": "neu-name"})
    await settle()
    assert f"{FOLDER}/link-alt-name" not in wiki.pages and "https://b.net" in wiki.pages[f"{FOLDER}/link-neu-name"]["body"]


async def test_no_writes_without_changes(server, wiki):  # noqa: F811
    sites.save([site()])
    await wiki_memory.sync(server.app)
    n = len(wiki.saves)
    await wiki_memory.sync(server.app)
    wiki_memory.reset()  # wie nach einem Neustart
    await wiki_memory.sync(server.app)
    assert len(wiki.saves) == n


async def test_own_notes_stay(server, wiki):  # noqa: F811
    wiki.pages[f"{FOLDER}/meine-notiz"] = {"body": "von Hand"}
    await wiki_memory.sync(server.app)
    assert f"{FOLDER}/meine-notiz" in wiki.pages and not wiki.deletes


async def test_offline_wiki_is_caught_up(server, wiki):  # noqa: F811
    wiki.down = True
    sites.save([site()])
    await wiki_memory.sync(server.app)  # darf nicht werfen
    assert not wiki.pages
    wiki.down = False
    await wiki_memory.sync(server.app)
    assert f"{FOLDER}/website-app-example-com" in wiki.pages


async def test_one_refused_page_does_not_stop_the_rest(server, wiki):  # noqa: F811
    wiki.refuse = "website-app"
    sites.save([site(), site(name="b", hostname="b.example.com")])
    await wiki_memory.sync(server.app)
    assert f"{FOLDER}/website-b-example-com" in wiki.pages and f"{FOLDER}/website-app-example-com" not in wiki.pages


async def test_off_without_wiki_url(server):  # noqa: F811
    assert not wiki_memory.enabled()
    await wiki_memory.sync(server.app)
    wiki_memory.kick(server.app)
    assert not wiki_memory._tasks


def test_plain_url_drops_secrets():
    assert wiki_memory.plain_url("https://nutzer:pass@a.net:8443/x?k=1#f") == "https://a.net:8443/x"
    assert wiki_memory.plain_url("git@github.com:a/b.git") == "git@github.com:a/b.git"
