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
