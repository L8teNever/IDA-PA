"""Startet den echten IDA-Wiki-Dienst (SQLite + Vault + Files im Temp-Ordner) auf einem freien Port.
Enthält eine strikte Netz-Sperre: jede Verbindung außerhalb von 127.0.0.1 lässt den Test sofort scheitern.
"""
import json
import os
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

IDA_TOKEN = "test-wiki-ida-token-1234567890"
MCP_TOKEN = "test-wiki-mcp-token-1234567890"
API = "/api/v1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeIda:
    """Nimmt `/api/v1/ext/wiki/ask`-Anfragen aus dem Wiki entgegen."""

    def __init__(self):
        self.received: list[dict] = []
        self.headers: list[dict] = []
        self.answer_text = "Hier ist Idas Antwort zur Seite."
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.received.append(body)
                outer.headers.append({k.lower(): v for k, v in self.headers.items()})
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"answer": outer.answer_text, "chat_id": "chat12345678"}).encode("utf-8"))

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture(scope="session", autouse=True)
def no_outside_network():
    """Garantiert absolute Lokalität: kein einziger Socket-Aufruf nach außen erlaubt."""
    real = socket.socket.connect

    def guard(self, addr):
        host = addr[0] if isinstance(addr, tuple) else str(addr)
        if self.family in (socket.AF_INET, socket.AF_INET6) and host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"Verbotene Verbindung nach außen: {addr}")
        return real(self, addr)

    socket.socket.connect = guard
    yield
    socket.socket.connect = real


@pytest.fixture(scope="session")
def fake_ida():
    return FakeIda()


@pytest.fixture(scope="session")
def server_env(fake_ida):
    tmp = Path(tempfile.mkdtemp(prefix="wiki-test-"))
    port = _free_port()
    os.environ.update({
        "DATA_DIR": str(tmp / "data"),
        "SCHEDULER_ENABLED": "false",
        "WIKI_TESTING": "1",
        "TIMEZONE": "Europe/Berlin",
        "WIKI_IDA_TOKEN": IDA_TOKEN,
        "WIKI_MCP_TOKEN": MCP_TOKEN,
        "WIKI_PUBLIC_URL": "https://wiki.kulbarts.com",
        "IDA_INTERNAL_URL": fake_ida.url,
    })
    import uvicorn
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app

    srv = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "Wiki-Testserver ist nicht gestartet"
    yield {"url": f"http://127.0.0.1:{port}", "data_dir": tmp / "data"}
    srv.should_exit = True
    th.join(5)


@pytest.fixture(scope="session")
def client(server_env):
    c = httpx.Client(base_url=server_env["url"], timeout=30)
    yield c
    c.close()


@pytest.fixture(scope="session")
def agent_client(server_env):
    def make(who: str = "main") -> httpx.Client:
        return httpx.Client(
            base_url=server_env["url"],
            timeout=30,
            headers={"Authorization": f"Bearer {IDA_TOKEN}", "X-Wiki-By": who},
        )
    return make


@pytest.fixture(scope="session")
def mcp_client(server_env):
    def make(token: str = MCP_TOKEN) -> httpx.Client:
        return httpx.Client(
            base_url=server_env["url"],
            timeout=30,
            headers={"Authorization": f"Bearer {token}"},
        )
    return make
