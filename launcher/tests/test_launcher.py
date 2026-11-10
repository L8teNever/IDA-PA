import pytest
from fastapi.testclient import TestClient

from app import cloudflare, icons
from app.config import get_settings
from app.main import app

APPS = [
    {"name": "Wiki", "host": "wiki.example.com", "logo": "", "protected": True, "service": "http://10.0.0.1:8443"},
    {"name": "offen.example.com", "host": "offen.example.com", "logo": "", "protected": False, "service": ""},
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    monkeypatch.setenv("SHOW_UNPROTECTED", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    cloudflare._cache.clear()

    async def fake_fetch():
        return {"apps": [dict(a) for a in APPS]}
    monkeypatch.setattr(cloudflare, "fetch", fake_fetch)
    yield TestClient(app)
    get_settings.cache_clear()


def test_health_und_startseite(client):
    assert client.get("/api/v1/health").json() == {"ok": True}
    assert client.get("/").status_code == 200


def test_apps_liste(client):
    d = client.get("/api/v1/apps").json()
    assert [a["host"] for a in d["apps"]] == ["offen.example.com", "wiki.example.com"] or len(d["apps"]) == 2
    wiki = next(a for a in d["apps"] if a["host"] == "wiki.example.com")
    assert wiki["url"] == "https://wiki.example.com" and wiki["protected"] and wiki["icon"].startswith("/api/v1/icon/wiki.example.com")


def test_verstecken_und_offene_ausblenden(client, monkeypatch):
    monkeypatch.setenv("HIDE", "wiki.*")
    get_settings.cache_clear(); cloudflare._cache.clear()
    assert [a["host"] for a in client.get("/api/v1/apps").json()["apps"]] == ["offen.example.com"]
    monkeypatch.setenv("HIDE", ""); monkeypatch.setenv("SHOW_UNPROTECTED", "false")
    get_settings.cache_clear(); cloudflare._cache.clear()
    assert [a["host"] for a in client.get("/api/v1/apps").json()["apps"]] == ["wiki.example.com"]


def test_ohne_token_meldet_fehler(client, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "")
    get_settings.cache_clear()
    d = client.get("/api/v1/apps").json()
    assert d["apps"] == [] and "TOKEN" in d["error"]


def test_icon_nur_fuer_bekannte_adressen(client, monkeypatch):
    assert client.get("/api/v1/icon/fremd.example.org").status_code == 404

    async def fake(app):
        return b"PNG", "image/png"
    monkeypatch.setattr(icons, "icon", fake)
    client.get("/api/v1/apps")
    r = client.get("/api/v1/icon/wiki.example.com")
    assert r.status_code == 200 and r.content == b"PNG" and r.headers["content-type"] == "image/png"


def test_icon_links_werden_gefunden():
    html = '<link rel="icon" href="/a.png"><link rel="apple-touch-icon" sizes="180x180" href="b.png"><link rel="mask-icon" href="m.svg">'
    assert icons._candidates(html, "http://x/app/") == ["http://x/app/b.png", "http://x/a.png"]


def test_ida_apps_stehen_oben_mit_schoenen_namen(client, monkeypatch):
    async def fake_fetch():
        return {"apps": [
            {"name": "zzz fremd", "host": "fremd.example.com", "logo": "", "protected": True, "service": "http://10.0.0.1:9000"},
            {"name": "docs.example.com", "host": "docs.example.com", "logo": "", "protected": True, "service": "http://10.0.0.1:8451"},
            {"name": "x", "host": "cleo.example.com", "logo": "", "protected": False, "service": "http://10.0.0.1:8450"},
            {"name": "y", "host": "html.example.com", "logo": "", "protected": False, "service": "http://10.0.0.1:8450"},
            {"name": "w", "host": "wiki.example.com", "logo": "", "protected": True, "service": "http://10.0.0.1:8443"},
        ]}
    monkeypatch.setattr(cloudflare, "fetch", fake_fetch)
    cloudflare._cache.clear()
    d = client.get("/api/v1/apps").json()["apps"]
    assert [(a["name"], a["ida"]) for a in d] == [("Wiki", True), ("Dokumente", True), ("HTML", True), ("zzz fremd", False)]
    assert d[2]["host"] == "html.example.com"


def test_nutzung_wird_gemerkt(client):
    assert client.post("/api/v1/visit", json={"host": "fremd.example.org"}).status_code == 404
    first = {a["host"]: a for a in client.get("/api/v1/apps").json()["apps"]}
    assert first["wiki.example.com"]["n"] == 0
    assert client.post("/api/v1/visit", json={"host": "wiki.example.com"}).json() == {"ok": True}
    client.post("/api/v1/visit", json={"host": "wiki.example.com"})
    w = {a["host"]: a for a in client.get("/api/v1/apps").json()["apps"]}["wiki.example.com"]
    assert w["n"] == 2 and w["last"] > 0


def test_titel_und_status_aus_cache(client):
    import json
    from app import status
    status._file("wiki.example.com").write_text(json.dumps({"title": "Mein Wiki", "up": True, "ts": 1}))
    status._file("offen.example.com").write_text(json.dumps({"title": "Offene Seite", "up": False, "ts": 1}))
    d = client.get("/api/v1/apps").json()
    wiki = next(a for a in d["apps"] if a["host"] == "wiki.example.com")
    offen = next(a for a in d["apps"] if a["host"] == "offen.example.com")
    assert offen["name"] == "Offene Seite" and offen["up"] is False
    assert wiki["up"] is True


def test_titel_wird_bereinigt():
    from app import status
    assert status.clean_title("  A &amp;\n B  ") == "A & B"


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_hintergruende(client):
    assert client.get("/api/v1/backgrounds").json() == {"items": []}
    assert client.post("/api/v1/backgrounds", content=b"kein bild").status_code == 400
    r = client.post("/api/v1/backgrounds", content=PNG)
    assert r.status_code == 200
    bid = r.json()["id"]
    assert [i["id"] for i in client.get("/api/v1/backgrounds").json()["items"]] == [bid]
    g = client.get(f"/api/v1/background/{bid}")
    assert g.status_code == 200 and g.headers["content-type"] == "image/png" and g.content == PNG
    assert client.get("/api/v1/background/..%2Fusage.json").status_code == 404
    assert client.delete(f"/api/v1/background/{bid}").status_code == 200
    assert client.get(f"/api/v1/background/{bid}").status_code == 404
