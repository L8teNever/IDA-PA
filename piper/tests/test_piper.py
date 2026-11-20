"""IDA Piper: Schnittstelle gegen einen Stimmen-Ersatz (Piper selbst rechnet hier nicht)."""
import io
import json
import wave

import pytest
from fastapi.testclient import TestClient

from app import voices
from app.config import get_settings
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPER_DIR", str(tmp_path))
    get_settings.cache_clear()
    voices._catalog = {}
    voices._loaded.clear()
    yield TestClient(create_app(autoload=False))
    get_settings.cache_clear()
    voices._catalog = {}


def test_tts_gibt_wav(client, monkeypatch):
    seen = []

    def fake(text, key, speed=1.0):
        seen.append((text, key, speed))
        return b"\x01\x00" * 2205, 22050
    monkeypatch.setattr(voices, "speak", fake)
    r = client.post("/api/v1/tts", json={"text": "  Hallo\n Welt ", "speed": 1.2})
    assert r.status_code == 200 and r.headers["x-sample-rate"] == "22050"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 22050 and w.getnframes() == 2205 and w.getsampwidth() == 2
    assert seen == [("Hallo Welt", "de_DE-thorsten-high", 1.2)]


def test_tts_fehler(client, monkeypatch):
    assert client.post("/api/v1/tts", json={"text": "  "}).status_code == 400
    assert client.post("/api/v1/tts", json={"text": "x" * 3000}).status_code == 413
    # unbekannte oder ungültige Stimme: 404 statt Absturz
    assert client.post("/api/v1/tts", json={"text": "Hallo", "voice": "../../etc"}).status_code == 404


def test_stimmenliste_aus_dem_volume(client, tmp_path):
    cat = {"de_DE-thorsten-high": {"name": "thorsten", "quality": "high", "language": {"code": "de_DE"},
                                   "files": {"de/de_DE/thorsten/high/de_DE-thorsten-high.onnx": {"size_bytes": 113895201}}},
           "en_US-amy-low": {"name": "amy", "quality": "low", "language": {"code": "en_US"}, "files": {}}}
    (tmp_path / "voices.json").write_text(json.dumps(cat))
    (tmp_path / "de_DE-thorsten-high.onnx").write_bytes(b"x")
    d = client.get("/api/v1/voices", params={"lang": "de"}).json()
    assert d["voices"] == [{"key": "de_DE-thorsten-high", "name": "thorsten", "language": "de_DE", "quality": "high", "speakers": 1,
                            "mb": 114, "downloaded": True}]
    assert len(client.get("/api/v1/voices").json()["voices"]) == 2


def test_health(client):
    d = client.get("/api/v1/health").json()
    assert d["ok"] and d["default"] == "de_DE-thorsten-high" and d["loaded"] == []
