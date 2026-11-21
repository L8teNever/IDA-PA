"""geraet_*-Tools: Geräte mit ida-connect über IDA Health – Ida im Chat (alle Geräte), Code-Threads (nur die des Projekts, mit Freigaben)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from .conftest import chat, wait
from .test_code import _start, code_on, text  # noqa: F401  (code_on ist ein Fixture)

API = "/api/v1"
DEVS = [{"id": "d1", "name": "Raspi", "online": True, "notes": "", "last_seen": 1,
         "info": {"os": "Debian 12", "arch": "aarch64", "mesh": {"ip": "100.96.0.5"}},
         "latest": {"cpu": 12.5, "mem": [2 * 1024 ** 3, 8 * 1024 ** 3], "temps": [["cpu", 51.0]], "disks": [["/", 10 * 1024 ** 3, 64 * 1024 ** 3]]}},
        {"id": "d2", "name": "NAS", "online": False, "notes": "", "info": {}, "latest": {}}]


class FakeHealth:
    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _handle(self):
                n = int(self.headers.get("content-length", 0) or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                outer.requests.append({"method": self.command, "path": u.path, "query": q, "body": body, "auth": self.headers.get("authorization")})
                if self.headers.get("authorization") != "Bearer test-health-token":
                    return self._json({"detail": "Nicht angemeldet"}, 401)
                parts = [unquote(x) for x in u.path.split("/")[3:]]  # nach /api/v1/
                if parts == ["devices"]:
                    return self._json({"devices": DEVS})
                d = next((x for x in DEVS if parts[1:2] and parts[1] in (x["id"], x["name"].lower(), x["name"])), None)
                if not d:
                    return self._json({"detail": "Gerät nicht gefunden"}, 404)
                rest = parts[2:]
                if not rest:
                    return self._json(d)
                if rest == ["exec"]:
                    return self._json({"code": 0, "stdout": "ausgabe von " + body["cmd"], "stderr": "", "timeout": False})
                if rest == ["history"]:
                    return self._json({"t": [1, 2], "cpu": [10, 20], "cpu_max": [15, 40], "temp_max": [50, 52], "load1": [0.5, 0.7]})
                if rest == ["docker"]:
                    return self._json({"docker": True, "stacks": [{"name": "web", "status": "running(1)", "files": ["/opt/web/compose.yaml"]}], "containers": []})
                if rest == ["docker", "stack"]:
                    return self._json({"ok": True, "output": f"{body['action']} {body['name']}"})
                if rest == ["docker", "logs"]:
                    return self._json({"logs": "start\nERROR kaputt\nlaeuft"})
                if rest == ["fs", "read"]:
                    return self._json({"path": q["path"], "content": "inhalt"})
                return self._json({"detail": "unbekannt"}, 404)

            do_GET = do_POST = do_PATCH = _handle

            def _json(self, data, status=200):
                b = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def health(client, monkeypatch):
    from app.config import get_settings
    fake = FakeHealth()
    monkeypatch.setattr(get_settings(), "health_url", fake.url)
    monkeypatch.setattr(get_settings(), "health_ida_token", "test-health-token")
    yield fake
    fake.server.shutdown()


def test_ida_sees_and_uses_devices(client, health):
    out = text(chat(client, text="TOOL geraete_liste {}"))
    assert '"name": "Raspi"' in out and '"mesh_ip": "100.96.0.5"' in out and '"cpu_prozent": 12.5' in out and '"NAS"' in out
    out = text(chat(client, text='TOOL geraet_befehl {"geraet": "Raspi", "befehl": "uptime"}'))
    assert "ausgabe von uptime" in out
    assert any(r["path"] == "/api/v1/devices/d1/exec" and r["body"]["cmd"] == "uptime" for r in health.requests)
    out = text(chat(client, text='TOOL geraet_status {"geraet": "d1", "stunden": 2}'))
    assert '"cpu_spitze": {"min": 15, "mittel": 27.5, "max": 40}' in out
    out = text(chat(client, text='TOOL geraet_docker_logs {"geraet": "d1", "container": "web-app-1", "grep": "error"}'))
    assert "ERROR kaputt" in out and "laeuft" not in out
    out = text(chat(client, text='TOOL geraet_status {"geraet": "gibtsnicht"}'))
    assert "nicht gefunden" in out


def test_devices_off_without_health(client):
    out = text(chat(client, text="TOOL geraete_liste {}"))
    assert "Raspi" not in out


def test_code_thread_only_project_devices_with_approval(client, code_on, health):
    p = client.post(f"{API}/code/projects", json={"name": "PiProjekt"}).json()
    assert client.get(f"{API}/code/devices").json()["devices"][0]["name"] == "Raspi"
    ev = chat(client, project_id=p["id"], text='TOOL geraet_docker {"geraet": "d1"}', permission_mode="full")
    assert "kein Gerät gewählt" in text(ev)
    assert client.patch(f"{API}/code/projects/{p['id']}", json={"devices": ["gibtsnicht"]}).status_code == 400
    r = client.patch(f"{API}/code/projects/{p['id']}", json={"devices": ["raspi"]}).json()  # per Name, gespeichert als id
    assert r["devices"] == ["d1"]
    ev = chat(client, project_id=p["id"], text="SHOWSYS hallo", permission_mode="full")
    assert "ida-connect" in text(ev) and "geraet_befehl" in text(ev)
    ev = chat(client, project_id=p["id"], text="TOOL geraete_liste {}", permission_mode="full")
    assert "Raspi" in text(ev) and "NAS" not in text(ev)
    ev = chat(client, project_id=p["id"], text='TOOL geraet_status {"geraet": "NAS"}', permission_mode="full")
    assert "gehört nicht zum Projekt" in text(ev)
    ev = chat(client, project_id=p["id"], text='TOOL geraet_docker_aktion {"geraet": "d1", "stack": "web", "aktion": "update"}', permission_mode="full")
    assert "update web" in text(ev)
    # „Nachfragen“: der Befehl wartet auf den Nutzer; ablehnen = nichts läuft
    n = len(health.requests)
    cid = _start(client, {"project_id": p["id"], "text": 'TOOL geraet_befehl {"geraet": "d1", "befehl": "rm -rf /"}'})
    a = wait(lambda: client.get(f"{API}/code/approvals?chat_id={cid}").json())[0]
    assert a["tool"] == "Gerät: Befehl auf Raspi" and a["input"]["befehl"] == "rm -rf /"
    client.post(f"{API}/code/approvals/{a['id']}", json={"decision": "deny"})
    wait(lambda: not client.get(f"{API}/chats/{cid}").json()["running"])
    assert "abgelehnt" in client.get(f"{API}/chats/{cid}").json()["messages"][-1]["text"]
    assert not any(x["path"].endswith("/exec") for x in health.requests[n:])
    assert client.patch(f"{API}/code/projects/{p['id']}", json={"devices": []}).json()["devices"] == []


def test_health_link_from_health_settings(client, monkeypatch):
    """„Mit IDA verbinden“ in Health: Adresse + Token landen in IDA und gelten vor der .env."""
    from app.config import get_settings
    fake = FakeHealth()
    monkeypatch.setattr(get_settings(), "health_url", "")
    monkeypatch.setattr(get_settings(), "health_ida_token", "")
    try:
        assert client.get(f"{API}/health-link").json()["enabled"] is False
        assert client.put(f"{API}/health-link", json={"url": "ftp://x", "token": "t" * 20}).status_code == 400
        r = client.put(f"{API}/health-link", json={"url": fake.url + "/", "token": "test-health-token"}).json()
        assert r["ok"] and r["url"] == fake.url
        assert client.get(f"{API}/health-link").json() == {"enabled": True, "url": fake.url, "token": True}
        out = text(chat(client, text="TOOL geraete_liste {}"))
        assert '"name": "Raspi"' in out
        assert fake.requests[-1]["auth"] == "Bearer test-health-token"
    finally:
        client.delete(f"{API}/health-link")
        fake.server.shutdown()
    assert client.get(f"{API}/health-link").json()["enabled"] is False
