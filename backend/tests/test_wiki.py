"""IDA ↔ Wiki-Dienst: wiki_*-Tools, Bootstrap-Link."""
from .conftest import chat

API = "/api/v1"
TOKEN = "test-wiki-token-123456"


def said(ev) -> str:
    return "".join(e.get("text", "") for e in ev)


def test_bootstrap_exposes_wiki_url(client):
    boot = client.get(f"{API}/bootstrap").json()
    assert boot.get("wiki_url") == "https://wiki.example.test"


def test_ida_searches_the_wiki_through_the_tool(client, wiki):
    wiki.clear()
    out = said(chat(client, text='TOOL wiki_search {"query": "homelab"}'))
    assert "Über mich" in out or "memory/" in out or "results" in out
    r = wiki.last("/search")
    assert r["method"] == "GET" and r["query"].get("q") == "homelab"
    assert r["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert r["headers"]["x-wiki-by"] == "ida"


def test_memory_save_goes_to_memory_path(client, wiki):
    wiki.clear()
    out = said(chat(client, text='TOOL wiki_memory_save {"category": "projekte", "slug": "homelab", "title": "Homelab", "body": "Docker Stack"}'))
    assert '"ok": true' in out or "Gespeichert" in out or "memory/projekte/homelab" in out
    r = wiki.last("/pages")
    assert r["method"] == "POST" and r["body"]["path"] == "memory/projekte/homelab"
    assert r["headers"]["x-wiki-by"] == "ida"


def test_wiki_down_gives_a_clear_error(client, wiki):
    wiki.clear()
    wiki.mode = "drop"
    try:
        out = said(chat(client, text='TOOL wiki_search {"query": "x"}'))
        assert "nicht erreichbar" in out.lower() or "fehler" in out.lower() or "wiki" in out.lower()
    finally:
        wiki.mode = "ok"
