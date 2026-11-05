"""Starts the real app (SQLite, fake Claude CLI) on a free port; tests talk HTTP to it."""
import os
import socket
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_bin(tmp: Path) -> str:
    script = HERE / "fake_claude.py"
    if sys.platform == "win32":
        p = tmp / "claude.cmd"
        p.write_text(f'@"{sys.executable}" "{script}" %*\r\n')
    else:
        p = tmp / "claude"
        p.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _fake_acp(tmp: Path) -> str:
    """Antigravity runtime dir with the fake ACP agent in place of agy_acp_server.par."""
    d = tmp / "agy-runtime"
    d.mkdir(parents=True, exist_ok=True)
    exe = d / "agy_acp_server.par"
    exe.write_text(f"#!{sys.executable}" + chr(10) + (HERE / "fake_acp.py").read_text(encoding="utf8"), encoding="utf8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    (d / "localharness_external").write_text("")
    (d / ".ok").write_text("test")
    return str(d)


_github = None
_kalender = None
_wiki = None
_voice = None
_dashboard = None


@pytest.fixture(scope="session")
def kalender(server):
    """Der Ersatz für den Kalender-Dienst (zeichnet auf, was IDA ihm schickt)."""
    return _kalender


@pytest.fixture(scope="session")
def wiki(server):
    """Der Ersatz für den Wiki-Dienst (zeichnet auf, was IDA ihm schickt)."""
    return _wiki


@pytest.fixture(scope="session")
def voice(server):
    """Der echte Voice-Container (app.voice) mit Gemini-Ersatz: {"url", "gemini", "app"}."""
    return _voice


@pytest.fixture(scope="session")
def dashboard(server):
    """Der Ersatz fürs Dashboard (Server-Stacks der Code-Projekte)."""
    return _dashboard


@pytest.fixture(scope="session")
def github(server):
    """The fake GitHub (Device Flow, API, bare repos) the app talks to."""
    return _github


@pytest.fixture(scope="session")
def server():
    global _github, _kalender, _wiki, _dashboard
    from .fake_dashboard import FakeDashboard
    from .fake_github import FakeGitHub
    from .fake_kalender import FakeKalender
    from .fake_wiki import FakeWiki
    tmp = Path(tempfile.mkdtemp(prefix="ida-test-"))
    port, vport = _free_port(), _free_port()
    _github = FakeGitHub(tmp / "github")
    _kalender = FakeKalender()
    _wiki = FakeWiki()
    _dashboard = FakeDashboard()
    os.environ.update({
        "DASHBOARD_URL": _dashboard.url,
        "KALENDER_URL": _kalender.url, "KALENDER_IDA_TOKEN": "test-kalender-token-123456",
        "WIKI_URL": _wiki.url, "WIKI_IDA_TOKEN": "test-wiki-token-123456",
        "WIKI_PUBLIC_URL": "https://wiki.example.test",
        "KALENDER_PUBLIC_URL": "https://kalender.example.test",
        "GITHUB_API_URL": _github.url, "GITHUB_URL": _github.url, "GITHUB_CLIENT_ID": "",
        "DATA_DIR": str(tmp / "data"), "CLAUDE_BIN": _fake_bin(tmp), "SCHEDULER_ENABLED": "false",
        "AGY_RUNTIME_DIR": _fake_acp(tmp),
        "INTERNAL_URL": f"http://127.0.0.1:{port}", "OLLAMA_URL": "http://127.0.0.1:9",
        # CI also runs the suite against PostgreSQL (TEST_DATABASE_URL); locally SQLite in the temp dir
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", ""),
        # Stella: der Voice-Container läuft im Test mit (Gemini wird ersetzt)
        "VOICE_URL": f"http://127.0.0.1:{vport}", "VOICE_SECRET": "test-voice-secret-123456",
        "VOICE_PUBLIC_URL": f"http://127.0.0.1:{vport}", "VOICE_IDA_URL": f"http://127.0.0.1:{port}",
        "GEMINI_API_KEY": "test-gemini-key", "STELLA_OWNER_NUMBER": "+4917012345678",
        # nie echte Zugangsdaten aus der Umgebung nutzen: sonst wählt der Test wirklich die Testnummer an
        "TELNYX_API_KEY": "", "TELNYX_CONNECTION_ID": "", "TELNYX_FROM_NUMBER": "", "VOICE_TELNYX_URL": "",
    })
    import uvicorn

    from app.main import create_app

    srv = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    global _voice
    from app.voice.main import create_app as create_voice

    from .fake_gemini import FakeGemini
    gem = FakeGemini()
    vapp = create_voice(connect=gem.connect)
    vsrv = uvicorn.Server(uvicorn.Config(vapp, host="127.0.0.1", port=vport, log_level="warning"))
    vth = threading.Thread(target=vsrv.run, daemon=True)
    vth.start()
    for _ in range(100):
        if vsrv.started:
            break
        time.sleep(0.1)
    _voice = {"url": f"http://127.0.0.1:{vport}", "gemini": gem, "app": vapp}
    yield f"http://127.0.0.1:{port}"
    vsrv.should_exit = True
    srv.should_exit = True
    th.join(5)
    vth.join(5)


@pytest.fixture(scope="session")
def client(server):
    c = httpx.Client(base_url=server, timeout=60)
    yield c
    c.close()


def chat(client, **body) -> list[dict]:
    import json
    with client.stream("POST", "/api/v1/chat", json=body) as r:
        assert r.status_code == 200, r.read()
        return [json.loads(line) for line in r.iter_lines() if line]


def wait(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    raise AssertionError("timeout")
