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


def test_wiki_read_sends_ref_and_drops_raw(client, wiki):
    wiki.clear()
    out = said(chat(client, text='TOOL wiki_read {"path": "privat/_start"}'))
    assert "Field required" not in out and "Fake" in out
    r = wiki.last("/page")
    assert r["query"] == {"ref": "privat/_start"}
    assert '"raw"' not in out


def test_private_save_writes_directly_and_keeps_frontmatter(client, wiki):
    wiki.clear()
    out = said(chat(client, text='TOOL wiki_private_save {"path": "privat/04-arbeit-projekte/projektarbeit/gliederung", "body": "1. Einleitung", "summary": "Gliederung"}'))
    assert "Privat gespeichert" in out
    assert not [r for r in wiki.requests if "/proposals" in r["path"]]  # keine Freigabe nötig
    r = wiki.last("/pages")
    assert r["method"] == "POST" and r["body"]["path"] == "privat/04-arbeit-projekte/projektarbeit/gliederung"
    assert r["body"]["frontmatter"]["tags"] == ["eigen"] and r["body"]["frontmatter"]["summary"] == "Gliederung"
    assert r["headers"]["x-wiki-by"] == "ida"


def test_private_save_new_page(client, wiki):
    wiki.clear()
    wiki.missing.add("privat/00-inbox/neu")
    try:
        out = said(chat(client, text='TOOL wiki_private_save {"path": "privat/00-inbox/neu.md", "title": "Neu", "body": "x"}'))
    finally:
        wiki.missing.clear()
    assert "Privat gespeichert" in out
    r = wiki.last("/pages")
    assert r["body"]["path"] == "privat/00-inbox/neu" and r["body"]["frontmatter"] == {}


def test_private_patch_move_folder_and_path_guard(client, wiki):
    wiki.clear()
    said(chat(client, text='TOOL wiki_private_patch {"path": "privat/00-inbox/x", "heading": "Ideen", "new_text": "- mehr"}'))
    r = wiki.last("/pages/patch")
    assert r["body"]["ref"] == "privat/00-inbox/x" and r["body"]["heading"] == "Ideen" and r["headers"]["x-wiki-by"] == "ida"
    out = said(chat(client, text='TOOL wiki_private_move {"path": "privat/00-inbox/x", "to_path": "privat/04-arbeit-projekte/x"}'))
    assert wiki.last("/pages/move")["body"]["to_path"] == "privat/04-arbeit-projekte/x" and "angepasste_links" in out
    said(chat(client, text='TOOL wiki_private_folder {"folder": "privat/04-arbeit-projekte/projektarbeit"}'))
    assert wiki.last("/folders")["body"]["folder"] == "privat/04-arbeit-projekte/projektarbeit"
    wiki.clear()
    out = said(chat(client, text='TOOL wiki_private_save {"path": "memory/kern/ueber-mich", "body": "x"}'))
    assert "Nur Pfade unter privat/" in out
    assert not [r for r in wiki.requests if r["path"] in ("/api/v1/page", "/api/v1/pages")]  # geht gar nicht erst raus
