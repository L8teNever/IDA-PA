"""Aufgaben-App (Tasks): tasks_*-Tools gegen einen kleinen Ersatz-Dienst und Stellas Aufgaben-Modus (Sprechen in der Tasks-App)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from .conftest import chat, wait


def said(ev: list[dict]) -> str:
    return "".join(e.get("text", "") for e in ev)

API = "/api/v1"
SECRET = {"x-voice-secret": "test-voice-secret-123456"}


class FakeTasks:
    """Merkt sich die Anfragen und spielt eine winzige Aufgabenliste."""

    def __init__(self):
        self.requests: list[dict] = []
        self.tasks: dict[str, dict] = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def _json(self, obj, code=200):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self):
                n = int(self.headers.get("content-length", 0) or 0)
                body = json.loads(self.rfile.read(n) or b"null") if n else None
                u = urlparse(self.path)
                outer.requests.append({"method": self.command, "path": u.path, "query": {k: v[0] for k, v in parse_qs(u.query).items()},
                                       "by": self.headers.get("x-tasks-by"), "auth": self.headers.get("authorization"), "body": body})
                if u.path == "/api/v1/tasks" and self.command == "POST":
                    if body.get("day") == "irgendwann":
                        return self._json({"detail": "day: Tag nicht verstanden: irgendwann"}, 400)
                    t = {"id": f"t{len(outer.tasks) + 1}", "title": body["title"], "note": body.get("note", ""), "priority": body.get("priority", 0),
                         "day": body.get("day"), "due": body.get("due"), "state": "geplant" if body.get("day") else "heute", "overdue": False,
                         "created_by": self.headers.get("x-tasks-by")}
                    outer.tasks[t["id"]] = t
                    return self._json({"task": t}, 201)
                if u.path == "/api/v1/tasks":
                    return self._json({"view": "offen", "today": "2026-11-06", "tasks": list(outer.tasks.values())})
                tid = u.path.rsplit("/", 1)[-1]
                t = outer.tasks.get(tid)
                if not t:
                    return self._json({"detail": "Aufgabe nicht gefunden"}, 404)
                if self.command == "PATCH":
                    if "done" in body:
                        t["state"] = "erledigt" if body["done"] else "heute"
                    t.update({k: v for k, v in body.items() if k != "done"})
                    return self._json({"task": t})
                if self.command == "DELETE":
                    outer.tasks.pop(tid)
                    return self._json({"ok": True, "task": t})
                return self._json({"task": t})

            do_GET = do_POST = do_PATCH = do_DELETE = _handle

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def tasks(client, monkeypatch):
    from app.config import get_settings
    fake = FakeTasks()
    monkeypatch.setattr(get_settings(), "tasks_url", fake.url)
    monkeypatch.setattr(get_settings(), "tasks_ida_token", "test-tasks-token")
    yield fake
    fake.server.shutdown()


def test_ida_creates_and_lists_tasks(client, tasks):
    out = said(chat(client, text='TOOL tasks_create {"title": "Zahnarzt anrufen", "priority": "hoch", "day": "morgen"}'))
    assert '"ok": true' in out and "Zahnarzt anrufen" in out
    post = next(r for r in tasks.requests if r["method"] == "POST")
    assert post["body"] == {"title": "Zahnarzt anrufen", "note": "", "day": "morgen", "due": None, "priority": 3}
    assert post["by"] == "ida" and post["auth"] == "Bearer test-tasks-token"
    out = said(chat(client, text='TOOL tasks_list {"view": "offen", "query": "zahn"}'))
    assert "prioritaet" in out and "hoch" in out
    get = [r for r in tasks.requests if r["method"] == "GET"][-1]
    assert get["query"]["view"] == "offen" and get["query"]["q"] == "zahn"


def test_task_errors_come_back_readable(client, tasks):
    out = said(chat(client, text='TOOL tasks_create {"title": "x", "day": "irgendwann"}'))
    assert "Tag nicht verstanden" in out
    out = said(chat(client, text='TOOL tasks_done {"id": "gibtsnicht"}'))
    assert "nicht gefunden" in out


def test_without_tasks_service_there_are_no_tools(client):
    from app.services.mcp import hub
    assert not any(n.startswith("tasks_") for _, n, _, _ in hub._spec(["ida"]))


def test_stella_in_task_mode_gets_own_prompt_and_only_task_tools(client, tasks):
    r = client.post(f"{API}/voice/internal/start", headers=SECRET,
                    json={"call_id": "wtask1", "direction": "web", "number": "", "owner": True, "mode": "tasks"}).json()
    assert r["mode"] == "tasks"
    names = {t["name"] for t in r["tools"]}
    assert {"tasks_create", "tasks_list", "tasks_done", "calendar_search", "calendar_events", "memory_save", "wissen_suchen"} <= names
    assert not names & {"tool_suchen", "tool_ausfuehren", "ida_fragen", "reminder_create", "calendar_event_create", "calendar_event_delete"}
    assert not any(n.startswith(("ida_1__", "google_", "homeassistant_")) for n in names)
    assert "Aufgaben-Modus" in r["context"] and "SO ARBEITEST DU HIER" in r["context"]
    assert "Ida hilft dir bei allem Größeren" not in r["context"]  # nicht der normale Gesprächs-Prompt
    by = {t["name"]: t for t in r["tools"]}
    assert "bestaetigt" not in by["tasks_create"]["parameters"]["properties"]   # sagen = Ja
    assert "bestaetigt" in by["tasks_delete"]["parameters"]["properties"]       # löschen fragt nach

    tool = lambda name, args: client.post(f"{API}/voice/internal/tool", headers=SECRET,  # noqa: E731
                                          json={"call_id": "wtask1", "name": name, "args": args}).json()
    ok = tool("tasks_create", {"title": "Mathe Seite 12", "due": "freitag"})
    assert "error" not in ok and "Mathe Seite 12" in ok["result"]
    assert next(r for r in tasks.requests if r["method"] == "POST")["by"] == "stella"
    assert "BESTAETIGUNG_NOETIG" in tool("tasks_delete", {"id": "t1"})["result"]
    assert "t1" in tasks.tasks
    assert "Aufgaben-Modus" in tool("tool_suchen", {"suche": "mail"})["error"]
    assert "Aufgaben-Modus" in tool("ida_fragen", {"frage": "Schick eine Mail"})["error"]
    client.post(f"{API}/voice/internal/finish", headers=SECRET, json={"call_id": "wtask1", "transcript": []})


def test_task_mode_only_for_the_owner(client, tasks):
    r = client.post(f"{API}/voice/internal/start", headers=SECRET,
                    json={"call_id": "ctask2", "direction": "out", "number": "+491111", "owner": False, "mode": "tasks"}).json()
    assert r["mode"] == "" and r["tools"] == []
    client.post(f"{API}/voice/internal/finish", headers=SECRET, json={"call_id": "ctask2", "transcript": []})


def test_task_mode_over_the_browser(client, voice, tasks):
    """Die Tasks-App verbindet sich mit ?mode=tasks: Gemini bekommt den Aufgaben-Prompt und nur die Aufgaben-Tools."""
    from websockets.sync.client import connect
    gem = voice["gemini"]
    gem.reset(tool=("tasks_create", {"title": "Wäsche aufhängen"}))
    url = client.post(f"{API}/voice/ticket").json()["url"]
    with connect(url + "&mode=tasks", open_timeout=10) as ws:
        assert json.loads(ws.recv(timeout=10))["type"] == "ready"
        ws.send(b"\x00\x01" * 1600)
        while True:
            m = ws.recv(timeout=15)
            if isinstance(m, str) and json.loads(m)["type"] == "end":
                break
    setup = gem.setups[-1]
    text = setup["systemInstruction"]["parts"][0]["text"]
    assert "Aufgaben-App „IDA Tasks“" in text and "SO ARBEITEST DU HIER" in text
    names = [d["name"] for d in setup["tools"][0]["functionDeclarations"]]
    assert "tasks_create" in names and "tool_suchen" not in names
    assert any(r["method"] == "POST" and r["body"]["title"] == "Wäsche aufhängen" for r in tasks.requests)
    titles = lambda: [client.get(f"{API}/chats/{c['chat_id']}").json()["title"]  # noqa: E731
                      for c in client.get(f"{API}/voice/calls").json() if c["chat_id"]]
    assert wait(lambda: any(t.startswith("Aufgaben-Gespräch") for t in titles()))
