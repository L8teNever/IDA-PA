"""Sandbox der Code-Threads: Dienst mit Docker-Ersatz, Projektkopie ohne Geheimnisse."""
import asyncio
import json
import subprocess
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.sandbox import main as sb
from app.services.code.sandbox import project_tar


def frame(text: str, kind: int = 1) -> bytes:
    data = text.encode()
    return bytes([kind, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


class FakeDocker:
    def __init__(self):
        self.running = False
        self.exists = False
        self.calls = []
        self.create = None
        self.memory = None
        self.exit = 0

    def __call__(self, r: httpx.Request) -> httpx.Response:
        path, m = r.url.path, r.method
        self.calls.append(f"{m} {path}")
        if path.startswith("/images/") and m == "GET":
            return httpx.Response(200, json={})
        if path == "/networks/create":
            return httpx.Response(409, json={})
        if path == "/containers/create":
            self.exists, self.create = True, json.loads(r.content)
            return httpx.Response(201, json={"Id": "c1"})
        if path.endswith("/start") and "containers" in path:
            self.running = True
            return httpx.Response(204)
        if path.endswith("/stop"):
            self.running = False
            return httpx.Response(204)
        if path.endswith("/update"):
            self.memory = json.loads(r.content)["Memory"]
            return httpx.Response(200, json={})
        if path.endswith("/exec"):
            return httpx.Response(201, json={"Id": "e1"})
        if path == "/exec/e1/start":
            return httpx.Response(200, stream=httpx.ByteStream(frame("hallo\n") + frame("fehler\n", 2)))
        if path == "/exec/e1/json":
            return httpx.Response(200, json={"ExitCode": self.exit})
        if path == "/containers/json":
            return httpx.Response(200, json=[{"State": "running" if self.running else "exited", "Labels": {"ida.sandbox.thread": "t1"}}] if self.exists else [])
        if path.endswith("/json"):
            if not self.exists:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"State": {"Running": self.running, "FinishedAt": "2026-01-01T00:00:00.0Z"}, "HostConfig": {"Memory": 2 * sb.GB}})
        return httpx.Response(204)


def boxes():
    d = FakeDocker()
    return sb.Sandboxes(httpx.MockTransport(d)), d


def test_runs_a_command_in_a_locked_down_container():
    box, d = boxes()
    out = asyncio.run(box.run("t1", "echo hallo", 30))
    assert out == {"exit": 0, "ausgabe": "hallo\nfehler\n", "gekuerzt": False}
    host = d.create["HostConfig"]
    assert host["Memory"] == 2 * sb.GB and host["MemorySwap"] == 2 * sb.GB and host["NanoCpus"] == 2 * 10 ** 9
    assert host["Binds"] == ["ida-sandbox-t1:/workspace"] and host["NetworkMode"] == "ida-sandbox"  # kein Ordner vom Server
    assert not host.get("Privileged") and "no-new-privileges:true" in host["SecurityOpt"]


def test_stops_after_idle_but_not_while_a_command_runs():
    box, d = boxes()
    asyncio.run(box.run("t1", "true", 5))
    later = time.time() + sb.IDLE_S + 5
    box.active["t1"] = 1
    asyncio.run(box.sweep(later))
    assert d.running
    box.active["t1"] = 0
    asyncio.run(box.sweep(time.time() + 60))
    assert d.running  # noch nicht 15 Minuten
    asyncio.run(box.sweep(later))
    assert not d.running
    asyncio.run(box.run("t1", "true", 5))  # Neustart beginnt wieder mit dem Grund-RAM
    assert d.memory == 2 * sb.GB


def test_deletes_after_a_week():
    box, d = boxes()
    asyncio.run(box.run("t1", "true", 5))
    d.running = False
    asyncio.run(box.sweep())
    assert "DELETE /containers/ida-sandbox-t1" in d.calls and "DELETE /volumes/ida-sandbox-t1" in d.calls


def test_api_needs_the_secret_and_limits_memory(monkeypatch):
    monkeypatch.setenv("SANDBOX_SECRET", "geheim")
    box, _ = boxes()
    c = TestClient(sb.create_app(box))
    assert c.post("/sandbox/t1/run", json={"befehl": "id"}).status_code == 403
    assert c.post("/sandbox/t1/run", json={"befehl": "id"}, headers={"x-sandbox-secret": "falsch"}).status_code == 403
    h = {"x-sandbox-secret": "geheim"}
    assert c.post("/sandbox/t1/run", json={"befehl": "id"}, headers=h).json()["exit"] == 0
    assert c.post("/sandbox/t1/memory", json={"gb": sb.MAX_GB + 1}, headers=h).status_code == 400
    assert c.post("/sandbox/t1/memory", json={"gb": 4}, headers=h).status_code == 200


def test_thread_names_cannot_reach_other_containers():
    with pytest.raises(HTTPException):
        sb.name_of("../ida-app-1")


def test_project_copy_leaves_secrets_out(tmp_path):
    import io
    import tarfile
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignoriert.txt\n")
    (tmp_path / "ignoriert.txt").write_text("x")
    (tmp_path / "app.py").write_text("print(1)")
    (tmp_path / ".env").write_text("KEY=geheim")
    (tmp_path / ".env.local").write_text("KEY=geheim")
    (tmp_path / ".env.example").write_text("KEY=")
    names = tarfile.open(fileobj=io.BytesIO(project_tar(tmp_path))).getnames()
    assert "app.py" in names and ".env.example" in names
    assert ".env" not in names and ".env.local" not in names and "ignoriert.txt" not in names
    assert not any(n.startswith(".git/") for n in names)
