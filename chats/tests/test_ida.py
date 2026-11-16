"""Chat „Ida“: Verlauf aus IDA-Chat + Postfach, Senden, An Ida schicken – gegen einen IDA-Ersatz."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

API = "/api/v1"


class FakeIda:
    def __init__(self):
        self.sent: list[dict] = []
        self.read_all = 0
        self.chat = {"id": "c1", "running": False, "messages": [
            {"id": "m1", "role": "user", "text": "Hi Ida", "at": "2026-11-16T10:00:00"},
            {"id": "m2", "role": "assistant", "text": "## Hallo\n**Simon**, wie kann ich helfen?\n- Punkt", "at": "2026-11-16T10:00:05"},
            {"id": "m3", "role": "system", "text": "intern", "at": "2026-11-16T10:00:06"}]}
        self.inbox = [{"id": "n1", "title": "Erinnerung", "text": "Müll rausbringen", "at": "2026-11-16T11:00:00", "from_name": "Ida", "read": False}]
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, d, code=200):
                b = json.dumps(d).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                p = self.path.split("?")[0]
                if p == "/api/v1/agents":
                    return self._json([{"id": "lea", "is_main": False, "name": "Lea"}, {"id": "ida", "is_main": True, "name": "Ida", "avatar": None}])
                if p == "/api/v1/chats/c1":
                    return self._json(outer.chat)
                if p == "/api/v1/messages/inbox":
                    return self._json({"unread": sum(not n["read"] for n in outer.inbox), "items": list(reversed(outer.inbox))})
                self._json({"detail": "x"}, 404)

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/v1/messages/read-all":
                    outer.read_all += 1
                    for x in outer.inbox:
                        x["read"] = True
                    return self._json({"ok": True})
                if self.path == "/api/v1/chat":
                    outer.sent.append(body)
                    out = (json.dumps({"chat": {"id": "c1"}}) + "\n" + json.dumps({"text": "ok"}) + "\n" + json.dumps({"done": {"ok": True}}) + "\n").encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/x-ndjson")
                    self.send_header("content-length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                self._json({"detail": "x"}, 404)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"


@pytest.fixture()
def fida(client, monkeypatch):
    from app import ida, push
    from app.config import get_settings
    f = FakeIda()
    monkeypatch.setattr(get_settings(), "ida_url", f.url)
    monkeypatch.setattr(ida, "_c", None)
    ida._agent.update(t=0.0, v=None)
    push._kv_set("ida_chat", "")
    push._kv_set("ida_seen", "")
    yield f
    f.srv.shutdown()


def test_ida_aus(client):
    assert client.get(f"{API}/ida").json() == {"on": False}


def test_ida_verlauf_und_zeile(client, fida):
    from app import push
    # noch kein Chat: nur die Meldung aus dem Postfach
    d = client.get(f"{API}/chats/ida/messages").json()
    assert [m["id"] for m in d["items"]] == ["nn1"] and d["items"][0]["text"] == "Erinnerung\nMüll rausbringen" and d["items"][0]["notice"]
    push._kv_set("ida_chat", "c1")
    d = client.get(f"{API}/chats/ida/messages").json()
    assert [m["id"] for m in d["items"]] == ["cm1", "cm2", "nn1"]   # nach Zeit, ohne System-Nachricht
    a = d["items"][1]
    assert not a["mine"] and a["sender"] == "Ida" and a["text"] == "Hallo\nSimon, wie kann ich helfen?\n• Punkt"   # Markdown → Klartext
    assert d["items"][0]["mine"] and d["items"][0]["at"].endswith("Z")
    r = client.get(f"{API}/ida").json()["chat"]
    assert (r["id"], r["title"], r["network"], r["unread"]) == ("ida", "Ida", "ida", 2)   # Meldung + Antwort
    assert r["last"]["text"] == "Erinnerung Müll rausbringen"
    assert client.post(f"{API}/chats/ida/read").json()["ok"] and fida.read_all == 1
    assert client.get(f"{API}/ida").json()["chat"]["unread"] == 0


def test_ida_senden(client, fida):
    from app import push
    assert client.post(f"{API}/chats/ida/send", json={"text": "Wie spät?"}).json()["ok"]
    assert fida.sent[-1] == {"agent_id": "ida", "text": "Wie spät?"}   # erster Chat: beim Haupt-Agenten anlegen
    assert push._kv("ida_chat") == "c1"
    client.post(f"{API}/chats/ida/send", json={"text": "Danke"})
    assert fida.sent[-1] == {"chat_id": "c1", "text": "Danke"}   # danach derselbe Chat
    assert client.post(f"{API}/chats/ida/send", json={"text": "  "}).status_code == 400


def test_an_ida_schicken(client, fida):
    body = {"chat_id": "!abc:beeper.local", "title": "Moro", "network": "Snapchat", "message": {"who": "Moro", "text": "Kommst du morgen?", "at": "2026-11-16T14:03:00Z"},
            "before": [{"who": "", "text": "Hey", "at": "2026-11-16T14:01:00Z", "mine": True}]}
    assert client.post(f"{API}/ida/forward", json=body).json()["ok"]
    t = fida.sent[-1]["text"]
    assert t.startswith("↪ Aus dem Chat „Moro“ über Snapchat:") and "[2026-11-16 14:03] Moro: Kommst du morgen?" in t
    assert "Beeper-Chat-ID: !abc:beeper.local" in t and "[2026-11-16 14:01] Simon: Hey" in t
    time.sleep(0.1)
