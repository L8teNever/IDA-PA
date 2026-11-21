import base64
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import get_settings
from app.store import Store, row_from_metrics

M = {"t": "m", "ts": 0, "cpu": 12.5, "cores": [10, 15], "mem": [500, 1000], "swap": [0, 0], "load": [0.5, 0.4, 0.3], "up": 99,
     "temps": [["cpu", 48.0], ["nvme", 40.0]], "fans": [["fan1", 1200]], "disks": [["/", 10, 100], ["/data", 5, 50]],
     "net": [["eth0", 1000, 200], ["wlan0", 10, 20]], "io": [["sda", 4096, 8192]]}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FRONTEND_DIR", str(FRONTEND))
    monkeypatch.setenv("IDA_TOKEN", "geheim")
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        yield c
    get_settings.cache_clear()


H = {"authorization": "Bearer geheim"}
FRONTEND = __import__("pathlib").Path(__file__).resolve().parents[2] / "frontend"


def test_row_from_metrics():
    r = row_from_metrics(M)
    assert r["cpu"] == 12.5 and r["mem"] == 500 and r["temp"] == 48.0
    assert r["rx"] == 1010 and r["tx"] == 220 and r["disk_used"] == 15 and r["disk_total"] == 150 and r["wr"] == 8192


def test_store_rollup(tmp_path):
    s = Store(tmp_path / "h.db", store_seconds=10)
    now = int(time.time()) // 300 * 300
    dev = s.redeem(s.create_pair_code("pi", False, "t"))["id"]
    for i in range(30):  # 5 Minuten, alle 10 s
        s.add(dev, {**M, "ts": now - 300 + i * 10, "cpu": float(i)})
    s.flush()
    s.compact(now + 1)
    raw = s.series(dev, now - 300, now, now=now + 1)
    assert len(raw["t"]) > 10 and raw["res"] == 0
    agg = s._q("SELECT n, cpu, cpu_max FROM samples WHERE dev = ? AND res = 1", (dev,))
    assert agg == [(30, 14.5, 29.0)]
    # zu oft gesendet -> nur alle 10 s gespeichert
    s.add(dev, {**M, "ts": now + 1})
    s.add(dev, {**M, "ts": now + 2})
    assert s.flush() == 1


def test_pair_code_once(tmp_path):
    s = Store(tmp_path / "h.db")
    code = s.create_pair_code("pi", True, "simon")
    d = s.redeem(code)
    assert d and d["mesh"] and s.auth(d["id"], d["secret"])
    assert s.redeem(code) is None
    assert not s.auth(d["id"], "falsch")


def test_auth_required(client):
    assert client.get("/api/v1/health").json() == {"ok": True}
    assert client.get("/api/v1/devices").status_code == 401
    assert client.get("/api/v1/devices", headers=H).json() == {"devices": []}


def test_pair_flow_csrf(client):
    cb = "http://192.168.1.5:1111/pair/done"
    r = client.get("/pair", params={"cb": cb, "state": "abc", "name": "pi"}, headers=H)
    assert r.status_code == 200 and "Gerät verbinden" in r.text
    csrf = r.text.split('name=csrf value="')[1].split('"')[0]
    # fremde Seite
    r = client.post("/pair/confirm", data={"csrf": csrf, "name": "pi"}, headers={**H, "origin": "https://boese.example"}, follow_redirects=False)
    assert r.status_code == 403
    r = client.post("/pair/confirm", data={"csrf": csrf, "name": "Pi Küche"}, headers=H, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(cb + "?state=abc&code=")
    # CSRF-Token nur einmal
    assert client.post("/pair/confirm", data={"csrf": csrf}, headers=H, follow_redirects=False).status_code == 400
    # nur /pair/done als Rücksprung
    assert client.get("/pair", params={"cb": "https://x.example/steal", "state": "a"}, headers=H).status_code == 400


class FakeAgent:
    """Simulierter ida-connect: beantwortet req und pty."""

    def __init__(self, ws):
        self.ws = ws
        self.stop = False

    def loop(self):
        while not self.stop:
            try:
                m = self.ws.receive_json()
            except Exception:  # noqa: BLE001
                return
            if m["t"] == "req":
                if m["op"] == "exec":
                    cmd = m["cmd"]
                    out = "ok\n"
                    if "docker compose ls" in cmd:
                        out = json.dumps([{"Name": "web", "Status": "running(1)", "ConfigFiles": "/opt/web/compose.yaml"}]) + "\n---\n" + \
                            json.dumps({"Names": "web-app-1", "Image": "nginx", "State": "running", "Status": "Up", "Ports": "",
                                        "Labels": "com.docker.compose.project=web,com.docker.compose.service=app"}) + "\n"
                    self.ws.send_json({"t": "res", "id": m["id"], "ok": True, "data": {"code": 0, "stdout": out, "stderr": "", "cmd": cmd}})
                elif m["op"] == "update_check":
                    self.ws.send_json({"t": "res", "id": m["id"], "ok": True, "data": {"current": "0.1.3", "latest": "0.1.5", "available": True, "error": ""}})
                elif m["op"] == "set_auto_update":
                    self.ws.send_json({"t": "res", "id": m["id"], "ok": True, "data": {"auto_update": m["on"]}})
                elif m["op"] == "fs_read":
                    self.ws.send_json({"t": "res", "id": m["id"], "ok": True, "data": {"size": 5, "d": base64.b64encode(b"hallo").decode(), "eof": True}})
                else:
                    self.ws.send_json({"t": "res", "id": m["id"], "ok": False, "error": "nö"})
            elif m["t"] == "pty_open":
                self.ws.send_json({"t": "pty_out", "sid": m["sid"], "d": base64.b64encode(b"$ ").decode()})
            elif m["t"] == "pty_in":
                self.ws.send_json({"t": "pty_out", "sid": m["sid"], "d": m["d"]})


def test_agent_end_to_end(client):
    code = client.post("/api/v1/pair-codes", json={"name": "Raspi", "mesh": False}, headers=H).json()["code"]
    with client.websocket_connect("/api/v1/agent/ws", headers={"authorization": f"Pair {code}"}) as ws:
        p = ws.receive_json()
        assert p["t"] == "paired" and p["name"] == "Raspi"
        ws.send_json({"t": "hello", "v": 1, "info": {"hostname": "raspi", "arch": "aarch64"}})
        assert ws.receive_json()["t"] == "config"
        ws.send_json({**M, "ts": int(time.time())})
        agent = FakeAgent(ws)
        th = threading.Thread(target=agent.loop, daemon=True)
        th.start()
        time.sleep(0.2)
        devs = client.get("/api/v1/devices", headers=H).json()["devices"]
        assert devs[0]["online"] and devs[0]["info"]["hostname"] == "raspi" and devs[0]["latest"]["cpu"] == 12.5
        dev = devs[0]["id"]
        r = client.post(f"/api/v1/devices/{dev}/exec", json={"cmd": "uptime"}, headers=H).json()
        assert r["stdout"] == "ok\n"
        # auch per Name
        assert client.post("/api/v1/devices/gibtsnicht/exec", json={"cmd": "x"}, headers=H).status_code == 404
        assert client.post("/api/v1/devices/Raspi/exec", json={"cmd": "x"}, headers=H).status_code == 200
        d = client.get(f"/api/v1/devices/{dev}/docker", headers=H).json()
        assert d["stacks"][0]["name"] == "web" and d["containers"][0]["stack"] == "web"
        r = client.post(f"/api/v1/devices/{dev}/docker/stack", json={"name": "web", "action": "update"}, headers=H).json()
        assert r["ok"]
        assert client.post(f"/api/v1/devices/{dev}/docker/stack", json={"name": "web", "action": "rm -rf"}, headers=H).status_code == 502
        assert client.get(f"/api/v1/devices/{dev}/fs/read", params={"path": "/etc/hostname"}, headers=H).json()["content"] == "hallo"
        assert client.get(f"/api/v1/devices/{dev}/fs/list", params={"path": "/"}, headers=H).status_code == 502
        h = client.get(f"/api/v1/devices/{dev}/history", params={"range": 600}, headers=H).json()
        assert h["cpu"] == [12.5]
        with client.websocket_connect(f"/api/v1/devices/{dev}/shell", headers=H) as sh:
            assert sh.receive_json() == {"t": "out", "d": base64.b64encode(b"$ ").decode()}
            sh.send_json({"t": "in", "d": "ls\r"})
            assert base64.b64decode(sh.receive_json()["d"]) == b"ls\r"
        acts = [a["action"] for a in client.get(f"/api/v1/devices/{dev}/audit", headers=H).json()["items"]]
        assert "befehl" in acts and "shell" in acts and "gekoppelt" in acts
        agent.stop = True
    # falsches Secret
    with client.websocket_connect("/api/v1/agent/ws", headers={"authorization": f"Bearer {dev}:falsch"}) as ws:
        with pytest.raises(Exception):
            ws.receive_json()


def test_ws_origin_check(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/v1/live", headers={"cf-access-jwt-assertion": "x", "origin": "https://boese.example"}) as ws:
            ws.receive_json()


def test_fail_closed_and_open_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FRONTEND_DIR", str(FRONTEND))
    monkeypatch.delenv("IDA_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        r = c.get("/api/v1/devices", headers={"cf-access-authenticated-user-email": "x@y"})
        assert r.status_code == 503 and "nicht abgesichert" in r.json()["detail"]
    monkeypatch.setenv("ALLOW_OPEN", "1")
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        assert c.get("/api/v1/devices").status_code == 200
    get_settings.cache_clear()


def test_origin_public_url_and_pair_warning(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://health.example.com")
    get_settings.cache_clear()
    from app import auth
    assert auth._origin_ok("https://health.example.com", "10.7.0.1:8455")
    assert auth._origin_ok(None, "x")
    assert not auth._origin_ok("https://boese.example", "10.7.0.1:8455")
    assert main._local_host("192.168.1.5:1111") and main._local_host("raspi.local:1111") and main._local_host("100.96.0.7:1111")
    assert not main._local_host("evil.example.com:1111") and not main._local_host("8.8.8.8:1111")
    r = client.get("/pair", params={"cb": "http://evil.example.com:1111/pair/done", "state": "a"}, headers=H)
    assert "nicht in einem privaten Netz" in r.text


def _fake_ida():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    got = []

    class H(BaseHTTPRequestHandler):
        def do_PUT(self):
            n = int(self.headers.get("content-length") or 0)
            got.append(json.loads(self.rfile.read(n)))
            self._j({"ok": True})

        def do_GET(self):
            self._j({"enabled": bool(got), "url": got[-1]["url"] if got else "", "token": bool(got)})

        def _j(self, d):
            b = json.dumps(d).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def test_settings_in_browser_with_setup_code(monkeypatch, tmp_path):
    from app import settings_ui
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FRONTEND_DIR", str(FRONTEND))
    monkeypatch.delenv("IDA_TOKEN", raising=False)
    monkeypatch.setenv("KEEP_DAYS", "30")
    monkeypatch.setenv("INTERNAL_URL", "http://health:8000")
    get_settings.cache_clear()
    srv, got = _fake_ida()
    with TestClient(main.app) as c:
        # nicht abgesichert: zu, nur die Anmeldeseite weiß Bescheid
        assert c.get("/api/v1/me").json() == {"who": None, "setup": True}
        assert c.get("/api/v1/settings").status_code == 503
        assert c.get("/").status_code == 200  # die Seite selbst ist frei
        code = (tmp_path / "setup-code.txt").read_text().strip()
        assert code == settings_ui.SETUP["code"]
        assert c.post("/api/v1/setup/login", json={"code": "falsch"}).status_code == 401
        assert c.post("/api/v1/setup/login", json={"code": code}).status_code == 200
        me = c.get("/api/v1/me").json()
        assert me["who"] == "einrichtung" and me["admin"] and me["setup"]
        v = c.get("/api/v1/settings").json()
        f = {x["key"]: x for g in v["groups"] for x in g["fields"]}
        assert f["keep_days"]["value"] == 30 and f["keep_days"]["source"] == "env" and f["store_seconds"]["source"] == "standard"
        assert f["ida_token"] == {**f["ida_token"], "set": False}
        # Speichern gilt sofort, .env bleibt darunter
        c.put("/api/v1/settings", json={"values": {"keep_days": 90, "public_url": "https://health.example.com/", "live_interval": 3}})
        assert get_settings().keep_days == 90 and get_settings().public_url == "https://health.example.com"
        assert main.STATE["store"].keep == 90 * 86400 and main.STATE["hub"].live_interval == 3
        assert c.put("/api/v1/settings", json={"values": {"keep_days": 0}}).status_code == 400
        assert c.put("/api/v1/settings", json={"values": {"gibtsnicht": 1}}).status_code == 400
        # Team ohne AUD würde aussperren -> abgelehnt
        assert c.put("/api/v1/settings", json={"values": {"access_team": "kulbarts"}}).status_code == 400
        # fremde Seite darf nichts ändern
        assert c.put("/api/v1/settings", json={"values": {"keep_days": 5}}, headers={"origin": "https://boese.example"}).status_code == 403
        # zurücksetzen -> wieder .env
        c.put("/api/v1/settings", json={"reset": ["keep_days"]})
        assert get_settings().keep_days == 30
        # Mit IDA verbinden: Token entsteht, IDA bekommt Adresse + Token
        c.put("/api/v1/settings", json={"values": {"ida_url": f"http://127.0.0.1:{srv.server_port}"}})
        r = c.post("/api/v1/settings/link-ida").json()
        tok = get_settings().ida_token
        assert r["ok"] and len(tok) == 64 and got[-1] == {"url": "http://health:8000", "token": tok}
        assert c.get("/api/v1/settings").json()["ida"]["linked"] is True
        # Geheimnisse kommen nie zurück
        assert tok not in c.get("/api/v1/settings").text
        # IDA (Token) darf Geräte bedienen, aber keine Einstellungen ändern
        assert c.get("/api/v1/devices", headers={"authorization": f"Bearer {tok}"}).status_code == 200
        assert c.get("/api/v1/settings", headers={"authorization": f"Bearer {tok}"}).status_code == 403
        # jetzt abgesichert; der Einrichtungs-Code bleibt Notfall-Zugang
        assert c.get("/api/v1/me").json()["setup"] is False
        assert c.post("/api/v1/setup/logout").status_code == 200
        assert c.get("/api/v1/settings").status_code == 401
    # nach Neustart: gespeichert, neuer Code
    old = code
    get_settings.cache_clear()
    with TestClient(main.app):
        assert get_settings().ida_token == tok and get_settings().public_url == "https://health.example.com"
        assert settings_ui.SETUP["code"] != old
    srv.shutdown()
    from app.config import set_overrides
    set_overrides({})


def test_agent_update_and_mesh_server(client):
    """Health schickt seine Mesh-Adresse an Geräte (beim Verbinden und bei Änderung); Update-Prüfung und Auto-Update am Gerät."""
    main.hub().mesh_server = "http://100.96.0.9:8000"
    code = client.post("/api/v1/pair-codes", json={"name": "Pi", "mesh": False}, headers=H).json()["code"]
    with client.websocket_connect("/api/v1/agent/ws", headers={"authorization": f"Pair {code}"}) as ws:
        assert ws.receive_json()["t"] == "paired"
        cfgs = [ws.receive_json(), ws.receive_json()]
        assert {"t": "config", "mesh_server": "http://100.96.0.9:8000"} in cfgs
        ws.send_json({"t": "hello", "v": 1, "info": {"hostname": "pi"}})
        # Einstellung „Adresse im Mesh“ von Hand -> geht an das Gerät
        main.STATE["store"].save_overrides({"mesh_address": "http://100.96.0.10:8000"}, [])
        from app import selfmesh, settings_ui
        settings_ui.apply(main.STATE["store"])
        assert selfmesh.address() == "http://100.96.0.10:8000"
        dev = client.get("/api/v1/devices", headers=H).json()["devices"][0]["id"]
        agent = FakeAgent(ws)
        seen = []
        orig = ws.receive_json

        def spy():
            m = orig()
            seen.append(m)
            return m
        ws.receive_json = spy
        th = threading.Thread(target=agent.loop, daemon=True)
        th.start()
        r = client.get(f"/api/v1/devices/{dev}/agent-update", headers=H).json()
        assert r["available"] and r["latest"] == "0.1.5"
        assert client.post(f"/api/v1/devices/{dev}/auto-update", json={"on": False}, headers=H).json() == {"auto_update": False}
        # Broadcast über den Hub (wie selfmesh.watch es macht)
        client.portal.call(main.hub().set_mesh_server, selfmesh.address())
        time.sleep(0.2)
        assert {"t": "config", "mesh_server": "http://100.96.0.10:8000"} in seen
        acts = [a["action"] for a in client.get(f"/api/v1/devices/{dev}/audit", headers=H).json()["items"]]
        assert "auto-update" in acts
        agent.stop = True


def test_mesh_guard_only_agent_path():
    """Über die Mesh-IP (lokale Adresse 100.96.x.x) nur der Agent-Pfad – nicht Oberfläche/API (dort ist kein Access davor)."""
    import asyncio

    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["path"])

    g = main.MeshGuard(inner)

    def run(path, server, typ="http"):
        sent = []

        async def send(m):
            sent.append(m)

        async def receive():
            return {"type": "http.request"}
        asyncio.run(g({"type": typ, "path": path, "server": server, "headers": []}, receive, send))
        return sent

    s = run("/api/v1/devices", ("100.96.0.7", 8000))
    assert s[0]["status"] == 403 and "/api/v1/devices" not in calls
    s = run("/api/v1/live", ("100.96.0.7", 8000), "websocket")
    assert s[0] == {"type": "websocket.close", "code": 4403}
    assert run("/api/v1/agent/ws", ("100.96.0.7", 8000), "websocket") == [] and "/api/v1/agent/ws" in calls
    assert run("/api/v1/devices", ("172.18.0.3", 8000)) == [] and "/api/v1/devices" in calls
    assert run("/", ("100.100.1.1", 8000))[0]["status"] == 403


def test_mesh_address_kept_while_warp_down(client):
    """Nach dem Start (Adresse noch unbekannt) schickt Health kein mesh_server; mit Knoten bleibt die letzte IP, auch wenn WARP kurz weg ist."""
    from app import selfmesh, settings_ui
    from app.hub import Hub
    assert Hub().mesh_server is None
    main.STATE["hub"].mesh_server = None
    code = client.post("/api/v1/pair-codes", json={"name": "X", "mesh": False}, headers=H).json()["code"]
    with client.websocket_connect("/api/v1/agent/ws", headers={"authorization": f"Pair {code}"}) as ws:
        assert ws.receive_json()["t"] == "paired"
        first = ws.receive_json()
        assert first["t"] == "config" and "mesh_server" not in first
    main.STATE["store"].save_overrides({"mesh_node": "n1", "mesh_ip": "100.96.0.3"}, [])
    settings_ui.apply(main.STATE["store"])
    selfmesh.STATE.update(status="getrennt", ip="")
    assert selfmesh.address() == "http://100.96.0.3:8000"
    main.STATE["store"].save_overrides({"mesh_node": ""}, [])
    settings_ui.apply(main.STATE["store"])
    assert selfmesh.address() == ""
    # IPv6 der WARP-Schnittstelle ist auch „Mesh“
    selfmesh.STATE["addrs"] = {"fd01::5"}
    assert selfmesh.in_mesh("fd01::5") and not selfmesh.in_mesh("fd01::6")
    selfmesh.STATE["addrs"] = set()


def test_mesh_node_reuses_existing_name(monkeypatch):
    """409 „gibt es schon“ → vorhandenen Knoten mit dem Namen nehmen statt Fehler."""
    import asyncio
    import httpx
    from app import mesh

    def handler(req: httpx.Request):
        if req.method == "POST":
            return httpx.Response(409, json={"success": False, "errors": [{"code": 1013, "message": "exists"}]})
        if req.url.path.endswith("/token"):
            return httpx.Response(200, json={"success": True, "result": "tok"})
        return httpx.Response(200, json={"success": True, "result": [{"id": "n1", "name": "ida-health"}]})

    monkeypatch.setattr(mesh, "enabled", lambda: True)
    monkeypatch.setattr(mesh, "_client", lambda: httpx.AsyncClient(base_url="https://cf.test", transport=httpx.MockTransport(handler)))

    async def acc(_c):
        return "acc"
    monkeypatch.setattr(mesh, "_account", acc)
    assert asyncio.run(mesh.create_node("ida-health")) == ("n1", "tok")
