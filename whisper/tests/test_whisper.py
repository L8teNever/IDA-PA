"""IDA Whisper: Schnittstelle gegen ein Modell-Ersatz (das echte Modell lädt hier nicht)."""
import pytest
from fastapi.testclient import TestClient

from app import model
from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app(autoload=False))


def test_health_zeigt_den_modellzustand(client):
    model._state.update(status="lädt", error="", model="large-v3-turbo")
    d = client.get("/api/v1/health").json()
    assert d["ok"] is True and d["status"] == "lädt" and d["model"] == "large-v3-turbo"


def test_noch_nicht_bereit_ist_503(client):
    model._state.update(status="lädt", error="")
    r = client.post("/api/v1/transkribieren", content=b"audio")
    assert r.status_code == 503 and "lädt" in r.json()["detail"]
    model._state.update(status="fehler", error="kein Speicher")
    assert "kein Speicher" in client.post("/api/v1/transkribieren", content=b"audio").json()["detail"]


def test_umwandeln(client, monkeypatch):
    seen = []

    def fake(data, language=""):
        seen.append((data, language))
        return {"text": "Hallo Welt", "language": "de", "duration": 1.5, "took": 0.3}
    monkeypatch.setattr(model, "transcribe", fake)
    model._state.update(status="bereit", error="")
    r = client.post("/api/v1/transkribieren", params={"language": "de"}, content=b"OggSdata")
    assert r.status_code == 200 and r.json()["text"] == "Hallo Welt" and seen == [(b"OggSdata", "de")]


def test_ablehnen(client, monkeypatch):
    model._state.update(status="bereit", error="")
    assert client.post("/api/v1/transkribieren", content=b"").status_code == 400
    assert client.post("/api/v1/transkribieren", params={"language": "Deutsch!"}, content=b"x").status_code == 422
    monkeypatch.setenv("WHISPER_MAX_MB", "1")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        assert client.post("/api/v1/transkribieren", content=b"x" * (1024 * 1024 + 1)).status_code == 413
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    # unlesbare Datei: 422 statt Absturz
    monkeypatch.setattr(model, "transcribe", lambda d, language="": (_ for _ in ()).throw(RuntimeError("kein Audio")))
    assert client.post("/api/v1/transkribieren", content=b"kaputt").status_code == 422
