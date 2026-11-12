"""Der echte Mail-Dienst (TestClient) gegen einen Gmail-/Google-Ersatz."""
import os

import pytest

from .fake_gmail import FakeGmail

_fake = None


@pytest.fixture(scope="session")
def fake():
    global _fake
    if _fake is None:
        _fake = FakeGmail()
    return _fake


@pytest.fixture(scope="session")
def client(fake):
    os.environ.update({"GOOGLE_API_URL": fake.url, "GOOGLE_API_TOKEN": "geheim", "GMAIL_API_URL": fake.url + "/gmail",
                       "GMAIL_UPLOAD_URL": fake.url + "/upload", "LIVE_POLL_S": "0.2",
                       "FRONTEND_DIR": os.path.join(os.path.dirname(__file__), "..", "..", "frontend")})
    from fastapi.testclient import TestClient

    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def reset(fake, client):
    from app import api
    fake.connected = True
    if api._gmail:
        api._gmail.forget()
    yield
