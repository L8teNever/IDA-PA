"""Startet den echten Kalender-Dienst (SQLite im Temp-Ordner) auf einem freien Port; die Tests sprechen HTTP mit ihm.

Dazu: ein Fake-IDA (nimmt den Webhook an) und eine Netz-Sperre – der Dienst darf nichts nach außen schicken.
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

TOKEN = "test-ida-token-1234567890"
MCP_TOKEN = "test-mcp-token-1234567890"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeIda:
    """Nimmt POSTs des Kalenders an. `fail = True` simuliert, dass IDA gerade nicht erreichbar ist."""

    def __init__(self):
        self.received: list[dict] = []
        self.headers: list[dict] = []
        self.fail = False
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                if outer.fail:
                    self.send_response(503)
                    self.end_headers()
                    return
                outer.received.append(body)
                outer.headers.append({k.lower(): v for k, v in self.headers.items()})
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/v1/ext/kalender"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture(scope="session", autouse=True)
def no_outside_network():
    """Der Kalender ist lokal: jede Verbindung außerhalb von 127.0.0.1 lässt den Test scheitern."""
    real = socket.socket.connect

    def guard(self, addr):
        host = addr[0] if isinstance(addr, tuple) else str(addr)
        if self.family in (socket.AF_INET, socket.AF_INET6) and host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"Verbindung nach außen: {addr}")
        return real(self, addr)

    socket.socket.connect = guard
    yield
    socket.socket.connect = real


@pytest.fixture(scope="session")
def ida():
    return FakeIda()


@pytest.fixture(scope="session")
def server(ida):
    tmp = Path(tempfile.mkdtemp(prefix="kalender-test-"))
    port = _free_port()
    os.environ.update({
        "DATA_DIR": str(tmp / "data"), "SCHEDULER_ENABLED": "false", "KALENDER_TESTING": "1", "TIMEZONE": "Europe/Berlin",
        "KALENDER_IDA_TOKEN": TOKEN, "KALENDER_MCP_TOKEN": MCP_TOKEN, "IDA_WEBHOOK_URL": ida.url, "KALENDER_STATE": "NW",
    })
    import uvicorn

    from app.main import create_app

    srv = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(5)


@pytest.fixture(scope="session")
def client(server):
    c = httpx.Client(base_url=server, timeout=30)
    yield c
    c.close()


@pytest.fixture(scope="session")
def agent(server):
    """Wie IDA, wenn Ida (oder ein anderer Agent) handelt: Bearer-Token + X-Kalender-By."""
    def make(who: str = "ida") -> httpx.Client:
        return httpx.Client(base_url=server, timeout=30, headers={"Authorization": f"Bearer {TOKEN}", "X-Kalender-By": who})
    return make


API = "/api/v1"


def mk(client, title="Test", start="2026-11-05T15:00", **kw) -> dict:
    r = client.post(f"{API}/events", json={"title": title, "start": start, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def day(client, d: str, **kw) -> list[dict]:
    r = client.get(f"{API}/events", params={"from": d, "to": d, **kw})
    assert r.status_code == 200, r.text
    return [e for e in r.json()["events"] if not e.get("holiday")]


def rng(client, a: str, b: str, **kw) -> list[dict]:
    r = client.get(f"{API}/events", params={"from": a, "to": b, **kw})
    assert r.status_code == 200, r.text
    return [e for e in r.json()["events"] if not e.get("holiday")]
