import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FRONTEND_DIR", str(tmp_path / "none"))
    from app import config
    config.get_settings.cache_clear()
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c, tmp_path
    config.get_settings.cache_clear()


def test_save_writes_both_files_and_only_non_default_settings(client):
    c, root = client
    r = c.post("/api/v1/docs", json={"title": "Aufsatz", "content": "# Hallo", "settings": {"size": 11, "font": "Georgia", "x": 1}})
    assert r.status_code == 201
    i = r.json()["id"]
    d = json.loads((root / "docs" / f"{i}.idadocs").read_text())
    assert d["settings"] == {"font": "Georgia"}          # size ist Standard, x unbekannt
    assert (root / "docs" / f"{i}.md").read_text() == "# Hallo"
    c.put(f"/api/v1/docs/{i}", json={"title": "Aufsatz", "content": "neu", "settings": {}})
    assert (root / "docs" / f"{i}.md").read_text() == "neu"
    assert c.get("/api/v1/docs").json()[0]["words"] == 1


def test_import_and_delete(client):
    c, root = client
    r = c.post("/api/v1/import", json={"name": "x.md", "text": "# Titel\ntext"})
    assert r.json()["title"] == "Titel"
    bad = c.post("/api/v1/import", json={"name": "x.idadocs", "text": "{}"})
    assert bad.status_code == 400
    i = r.json()["id"]
    assert c.delete(f"/api/v1/docs/{i}").status_code == 204
    assert c.get(f"/api/v1/docs/{i}").status_code == 404


def test_backup_zip_and_snapshot(client):
    import io
    import zipfile
    c, root = client
    i = c.post("/api/v1/docs", json={"title": "A", "content": "x"}).json()["id"]
    z = zipfile.ZipFile(io.BytesIO(c.get("/api/v1/backup").content))
    assert sorted(z.namelist()) == [f"{i}.idadocs", f"{i}.md"]
    assert len(list((root / "versions" / i).glob("*.idadocs"))) == 1


def test_sources_saved_kept_and_cleaned(client):
    c, root = client
    src = [{"key": "mueller2020", "type": "buch", "author": "Müller, Anna", "year": "2020", "title": "Buch", "quotes": [{"text": "Zitat", "page": "4"}, {"text": " "}]},
           {"key": "mueller2020", "title": "doppelt"}, {"key": "", "title": "ohne Schlüssel"}, "kaputt"]
    i = c.post("/api/v1/docs", json={"title": "A", "content": "x", "sources": src}).json()["id"]
    d = c.get(f"/api/v1/docs/{i}").json()
    assert [s["key"] for s in d["sources"]] == ["mueller2020"] and d["sources"][0]["quotes"] == [{"text": "Zitat", "page": "4"}]
    c.put(f"/api/v1/docs/{i}", json={"title": "A", "content": "y"})          # ohne sources → bleiben erhalten
    assert len(c.get(f"/api/v1/docs/{i}").json()["sources"]) == 1


def test_lookup_blocks_internal_addresses_and_parses_pages(client):
    c, _ = client
    assert c.get("/api/v1/lookup", params={"q": "http://127.0.0.1:8000/"}).status_code == 400
    assert c.get("/api/v1/lookup", params={"q": "hallo"}).status_code == 400
    from app import lookup
    r = lookup.parse_page('<html><head><title>Fallback</title><meta property="og:title" content="Ein &amp; Titel"><meta name="author" content="Lena Kurz"><meta property="article:published_time" content="2023-05-01T10:00"></head></html>', "https://www.beispiel.de/a")
    assert (r["title"], r["author"], r["year"], r["publisher"]) == ("Ein & Titel", "Lena Kurz", "2023", "beispiel.de")
