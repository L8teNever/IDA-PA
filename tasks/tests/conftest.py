"""Der echte Dienst mit einer SQLite-Datei im Temp-Ordner, angesprochen über HTTP (TestClient, mit Migrationen)."""
import os
import tempfile

import pytest

TOKEN = "test-ida-token-1234567890"
SECRET = "test-voice-secret"


@pytest.fixture(scope="session")
def client():
    d = tempfile.mkdtemp(prefix="tasks-test-")
    os.environ.update({"DATA_DIR": d, "TASKS_IDA_TOKEN": TOKEN, "VOICE_SECRET": SECRET, "VOICE_PUBLIC_URL": "https://voice.example.com",
                       "FRONTEND_DIR": os.path.join(os.path.dirname(__file__), "..", "..", "frontend")})
    from fastapi.testclient import TestClient

    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def clean(client):
    for v in ("alle",):
        for t in client.get("/api/v1/tasks", params={"view": v}).json()["tasks"]:
            client.delete(f"/api/v1/tasks/{t['id']}")
    yield


def ida(by: str = "ida") -> dict:
    return {"authorization": f"Bearer {TOKEN}", "x-tasks-by": by}
