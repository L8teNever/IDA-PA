"""WebUntis-Nachrichten als Chats – gegen einen WebUntis-Ersatz (Anmeldung, Postfach, Lehrkräfte, Antworten, neue Nachricht)."""
import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

API = "/api/v1"


def jwt(claims: dict) -> str:
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b({'alg': 'none'})}.{b(claims)}.x"


class FakeUntis:
    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.read: set[int] = set()
        self.headers: list[dict] = []
        self.inbox = [
            {"id": 11, "subject": "Projektarbeit", "contentPreview": "Bitte Themenblatt …", "sender": {"displayName": "Samarajiwa Konrad"},
             "sentDateTime": "2026-11-16T09:00:00", "isMessageRead": False, "isReplyAllowed": True},
            {"id": 12, "subject": "Ausflug", "contentPreview": "Morgen …", "sender": {"displayName": "Müller Anna"},
             "sentDateTime": "2026-11-15T12:00:00", "isMessageRead": True, "isReplyAllowed": False}]
        self.details = {
            11: {"id": 11, "subject": "Projektarbeit", "content": "<p>Bitte Themenblatt<br>unterschreiben lassen.</p>", "sender": {"displayName": "Samarajiwa Konrad"},
                 "sentDateTime": "2026-11-16T09:00:00", "isReplyAllowed": True,
                 "replyHistory": [{"id": 9, "subject": "Frage", "content": "Wann treffen wir uns?", "sender": {"displayName": "Kulbarts Simon"}, "sentDateTime": "2026-11-14T08:00:00"}]},
            12: {"id": 12, "subject": "Ausflug", "content": "Morgen 8 Uhr", "sender": {"displayName": "Müller Anna"}, "sentDateTime": "2026-11-15T12:00:00", "isReplyAllowed": False},
            30: {"id": 30, "subject": "Krank", "content": "Ich bin heute krank.", "sender": {"displayName": "Kulbarts Simon"}, "sentDateTime": "2026-11-13T07:00:00",
                 "recipientPersons": [{"personId": 77, "displayName": "Weber Tom"}]}}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _out(self, d, code=200, ctype="application/json"):
                b = (json.dumps(d) if not isinstance(d, str) else d).encode()
                self.send_response(code)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                p = self.path.split("?")[0]
                if p == "/WebUntis/api/token/new":
                    ok = "JSESSIONID=s1" in (self.headers.get("cookie") or "")
                    return self._out(jwt({"tenant_id": "4711", "exp": 9999999999}) if ok else "<html>", ctype="text/plain")
                outer.headers.append(dict(self.headers))
                if self.headers.get("authorization", "").count(".") != 2:
                    return self._out({}, 401)
                if p.endswith("/v1/messages"):
                    return self._out({"incomingMessages": [{**m, "isMessageRead": m["isMessageRead"] or m["id"] in outer.read} for m in outer.inbox],
                                      "readConfirmationMessages": []})
                if p.endswith("/v1/messages/sent"):
                    return self._out({"sentMessages": [{"id": 30, "subject": "Krank", "contentPreview": "Ich bin heute krank.", "sentDateTime": "2026-11-13T07:00:00"}]})
                if p.endswith("/recipients/static/teachers"):
                    return self._out([{"type": "TEACHERS", "persons": [{"personId": 55, "displayName": "Samarajiwa Konrad"}, {"personId": 77, "displayName": "Weber Tom"},
                                                                      {"personId": 88, "displayName": "Neu Lena"}]}])
                if (m := re.search(r"/v1/messages/(\d+)/reply-form$", p)):
                    return self._out({"subject": "AW: " + outer.details[int(m[1])]["subject"]})
                if (m := re.search(r"/v1/messages/(\d+)$", p)):
                    outer.read.add(int(m[1]))
                    return self._out(outer.details[int(m[1])])
                self._out({}, 404)

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n)
                p = self.path.split("?")[0]
                if p == "/WebUntis/jsonrpc.do":
                    d = json.loads(raw)
                    if d["params"]["password"] != "geheim":
                        return self._out({"error": {"code": -8504, "message": "bad credentials"}})
                    self.send_response(200)
                    b = json.dumps({"result": {"sessionId": "s1"}}).encode()
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(b)))
                    self.end_headers()
                    return self.wfile.write(b)
                m = re.search(rb'name="request"\r\nContent-Type: application/json\r\n\r\n(.*?)\r\n--', raw, re.S)
                outer.posts.append((p, json.loads(m[1]) if m else {}))
                self._out({})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.srv.server_port}"


@pytest.fixture()
def fu(client, monkeypatch):
    from app import push, untis
    f = FakeUntis()
    monkeypatch.setattr(untis, "HOST", re.compile(r"^127\.0\.0\.1:\d+$"))
    monkeypatch.setattr(untis, "base_url", lambda server: f"http://{server}/WebUntis")
    yield f
    client.delete(f"{API}/untis")
    push._kv_set("untis_seen", "")
    f.srv.shutdown()


def test_untis_anmelden(client, fu):
    assert client.get(f"{API}/untis").json() == {"on": False}
    r = client.put(f"{API}/untis", json={"server": fu.host, "school": "bk-pp", "school_name": "BK Paulinenpflege", "user": "KulSim", "password": "falsch"})
    assert r.status_code == 401 and "abgelehnt" in r.json()["detail"]
    d = client.put(f"{API}/untis", json={"server": fu.host, "school": "bk-pp", "school_name": "BK Paulinenpflege", "user": "KulSim", "password": "geheim"}).json()
    assert d == {"on": True, "user": "KulSim", "school": "BK Paulinenpflege", "school_login": "bk-pp", "server": fu.host}
    assert "geheim" not in json.dumps(client.get(f"{API}/untis").json())   # Passwort geht nie zurück an die Seite


def test_untis_chats_und_verlauf(client, fu):
    client.put(f"{API}/untis", json={"server": fu.host, "school": "bk-pp", "user": "KulSim", "password": "geheim"})
    items = client.get(f"{API}/untis/chats").json()["items"]
    assert [(c["id"], c["title"], c["unread"]) for c in items] == [
        ("untis:samarajiwa-konrad", "Samarajiwa Konrad", 1), ("untis:muller-anna", "Müller Anna", 0), ("untis:weber-tom", "Weber Tom", 0)]
    assert items[0]["at"] == "2026-11-16T08:00:00.000Z" and items[0]["network"] == "untis"   # deutsche Zeit → UTC
    assert items[2]["last"]["mine"]
    assert 11 not in fu.read   # die Liste allein markiert nichts als gelesen
    assert fu.headers[-1].get("tenant-id") == "4711"
    d = client.get(f"{API}/chats/untis:samarajiwa-konrad/messages").json()
    assert [(m["id"], m["mine"]) for m in d["items"]] == [("u9", True), ("u11", False)]   # mit Verlauf (deine Frage davor)
    assert d["items"][1]["text"] == "Projektarbeit\n\nBitte Themenblatt\nunterschreiben lassen." and d["items"][1]["sender"] == "Samarajiwa Konrad"
    assert 11 in fu.read   # geöffnet = gelesen
    t = client.get(f"{API}/untis/teachers").json()["items"]
    assert [x["title"] for x in t] == ["Neu Lena", "Samarajiwa Konrad", "Weber Tom"]


def test_untis_antworten_und_neu(client, fu):
    client.put(f"{API}/untis", json={"server": fu.host, "school": "bk-pp", "user": "KulSim", "password": "geheim"})
    assert client.post(f"{API}/chats/untis:samarajiwa-konrad/send", json={"text": "Mach ich <morgen>\nDanke"}).json()["how"] == "reply"
    p, req = fu.posts[-1]
    assert p == "/WebUntis/api/rest/view/v2/messages/11/reply" and req == {"subject": "AW: Projektarbeit", "content": "<p>Mach ich &lt;morgen&gt;<br>Danke</p>"}
    # Antworten nicht erlaubt (Müller) und noch nie geschrieben (Neu): neue Nachricht an die Lehrkraft
    assert client.post(f"{API}/chats/untis:neu-lena/send", json={"text": "Hallo Frau Neu, kurze Frage"}).json()["how"] == "new"
    p, req = fu.posts[-1]
    assert p == "/WebUntis/api/rest/view/v2/messages" and req["recipientPersonIds"] == [88] and req["recipientOption"] == "TEACHER"
    assert req["subject"] == "Hallo Frau Neu, kurze Frage"
    # gezielt auf eine Nachricht antworten
    client.post(f"{API}/chats/untis:samarajiwa-konrad/send", json={"text": "ok", "reply_to": "u11"})
    assert fu.posts[-1][0].endswith("/v2/messages/11/reply")
    assert client.post(f"{API}/chats/untis:../send", json={"text": "x"}).status_code in (400, 404)
