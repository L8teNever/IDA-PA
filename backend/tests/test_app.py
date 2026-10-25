import json
import sys

import httpx
import pytest

from .conftest import chat, wait

API = "/api/v1"


def test_no_web_login(server):
    """The web UI has no login of its own: API, pages and files work without a session."""
    with httpx.Client(base_url=server) as c:
        assert c.get(f"{API}/bootstrap").status_code == 200
        assert c.get("/einstellungen/agents", follow_redirects=False).status_code == 200
        assert c.post(f"{API}/auth/login", json={"password": "x"}).status_code in (404, 405)  # endpoint is gone


def test_pages_get_the_app(client):
    for url in ("/", "/chats", "/einstellungen/agents/lea", "/routinen/neu"):
        r = client.get(url)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    # files are linked under a version path (new with every update) and may be cached for good
    import re
    ver = re.search(r'"/s/(\w+)/js/main\.js"', r.text).group(1)
    a = client.get(f"/s/{ver}/css/app.css")
    assert a.status_code == 200 and "immutable" in a.headers["cache-control"]
    assert client.get("/static/css/app.css").headers["cache-control"] == "no-cache"
    assert client.get("/api/v1/gibtsnicht").status_code == 404


def test_installable_as_app(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    data = m.json()
    assert data["display"] == "standalone" and {"192x192", "512x512"} <= {i["sizes"] for i in data["icons"]}
    for i in data["icons"]:
        assert client.get(i["src"]).headers["content-type"] == "image/png"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert 'rel="manifest"' in client.get("/").text


def test_seed(client):
    b = client.get(f"{API}/bootstrap").json()
    assert [a["id"] for a in b["agents"]] == ["ida", "stella", "lea"]
    assert b["agents"][0]["is_main"] and b["accounts"][0]["id"] == "a1"
    assert {s["id"] for s in b["skills"]} >= {"zusammenfassen", "merken"}


def test_chat_stream_persist_resume_and_skill(client):
    ev = chat(client, text="Hallo Ida")
    cid = ev[0]["chat"]["id"]
    assert "".join(e.get("text", "") for e in ev).startswith("[a1|claude-sonnet-5-5] Hallo Ida")
    assert ev[-1]["done"]["ok"]
    ev = chat(client, chat_id=cid, text="SHOWSYS weiter", skill="zusammenfassen")
    text = "".join(e.get("text", "") for e in ev)
    assert "Skill /zusammenfassen" in text and "Persoenlichkeit" not in text.split("SYSTEM:")[0]
    c = client.get(f"{API}/chats/{cid}").json()
    assert [m["role"] for m in c["messages"]] == ["user", "assistant", "user", "assistant"]
    assert c["messages"][2]["meta"]["skill"] == "zusammenfassen"
    assert client.get(f"{API}/chats?q=weiter").json()[0]["id"] == cid
    act = client.get(f"{API}/activity").json()  # runs are over: nobody working, Ida's last run went well
    assert act["agents"] == {} and act["done"]["ida"]["ok"] is True


def test_activity_steps():
    from app.services import activity
    rid = activity.start("stella", "run")
    activity.step(rid, "tool", "stella_call", "telefon")
    activity.step(rid, "writing")  # the last tool stays visible while the answer is written
    a = activity.snapshot()["agents"]["stella"]
    assert (a["state"], a["tool"], a["kind"]) == ("tool", "stella_call", "run")
    activity.end(rid, False)
    snap = activity.snapshot()
    assert "stella" not in snap["agents"] and snap["done"]["stella"]["ok"] is False


def test_hub_tools_rights_and_memory(client):
    # main agent saves into its own memory and into Lea's (foreign) memory
    ev = chat(client, text='TOOL memory_save {"text": "Der Nutzer trinkt Kaffee ohne Zucker", "type": "preference"}')
    assert '"ok": true' in "".join(e.get("text", "") for e in ev)
    ev = chat(client, text='TOOL memory_save {"text": "Lea soll morgens melden", "scope": "lea"}')
    assert "NICHT dein eigenes" in "".join(e.get("text", "") for e in ev)
    assert client.get(f"{API}/memory?scope=ida&q=Kaffees").json()[0]["text"].startswith("Der Nutzer trinkt")
    # a sub-agent may not touch other memories nor manage agents
    ev = chat(client, agent_id="lea", text='TOOL memory_save {"text": "x", "scope": "ida"}')
    assert "Keine Berechtigung" in "".join(e.get("text", "") for e in ev)
    ev = chat(client, agent_id="lea", text='TOOL agent_create {"name": "X", "role": "y"}')
    assert "Keine Berechtigung" in "".join(e.get("text", "") for e in ev)
    # the memory block is in the next prompt
    ev = chat(client, text="SHOWSYS Kaffee?")
    assert "Kaffee ohne Zucker" in "".join(e.get("text", "") for e in ev)


def test_agents_search_and_read_other_chats(client):
    # a chat with the main agent mentions something; another agent finds it and reads the context
    chat(client, text="Merk dir den Zahnarzttermin Zebrafink am Dienstag")
    ev = chat(client, agent_id="lea", text='TOOL chat_search {"query": "zahnarzttermin zebrafink"}')
    out = "".join(e.get("text", "") for e in ev)
    assert "Zebrafink" in out and '"chat_id"' in out
    cid = out.split('"chat_id": "', 1)[1].split('"', 1)[0]
    ev = chat(client, agent_id="lea", text=f'TOOL chat_read {{"chat_id": "{cid}"}}')
    read = "".join(e.get("text", "") for e in ev)
    assert "Zahnarzttermin Zebrafink" in read and '"von": "Nutzer"' in read
    ev = chat(client, agent_id="lea", text='TOOL chat_read {"chat_id": "gibtsnicht"}')
    assert "nicht gefunden" in "".join(e.get("text", "") for e in ev)


def test_notify_and_inbox(client):
    chat(client, agent_id="lea", text='TOOL notify_user {"text": "Neue Mail vom Chef", "title": "Mail", "priority": "high"}')
    home = client.get(f"{API}/home").json()
    assert home["notices"][0]["title"] == "Mail" and home["notices"][0]["from"] == "lea"
    client.post(f"{API}/messages/read-all")
    assert client.get(f"{API}/home").json()["notices"] == []


def test_delegate_wakes_agent_and_reply_comes_back(client):
    chat(client, text='TOOL agent_delegate {"to": "stella", "task": "Sag hallo"}')
    msgs = wait(lambda: [m for m in client.get(f"{API}/messages?box=all").json() if m["from"] == "stella" and m["kind"] == "result"])
    assert "Sag hallo" in msgs[0]["text"] and msgs[0]["to"] == "ida"
    assert client.get(f"{API}/agents/stella").json()["runs"][0]["trigger"] == "Nachricht"


def test_account_fallback(client):
    client.post(f"{API}/accounts", json={"name": "Zweit"})
    ev = chat(client, text="LIMIT bitte")
    assert any("weiter mit Zweit" in e.get("sys", "") for e in ev)
    assert "".join(e.get("text", "") for e in ev).startswith("[a2|")
    r = client.post(f"{API}/routines", json={"name": "L", "prompt": "LIMIT routine", "interval_min": 60}).json()
    client.post(f"{API}/routines/{r['id']}/run")
    run = wait(lambda: client.get(f"{API}/routines/{r['id']}").json()["runs"])
    assert run[0]["ok"] and "Account 1 ist am Limit" in run[0]["text"] and run[0]["account"] == "Zweit"


def test_skills_crud(client):
    s = client.post(f"{API}/skills", json={"name": "Einkauf Liste", "body": "Mach eine Liste", "agents": ["lea"]}).json()
    assert s["id"] == "einkauf-liste" and s["agents"] == ["lea"]
    assert client.patch(f"{API}/skills/{s['id']}", json={"enabled": False}).json()["enabled"] is False
    assert client.post(f"{API}/skills", json={"name": "", "body": "x"}).status_code == 400
    assert client.delete(f"{API}/skills/{s['id']}").json()["ok"]


def test_agent_crud_and_delete_keeps_history(client):
    a = client.post(f"{API}/agents", json={"name": "Mia", "role": "Schule", "palette": "lila"}).json()
    assert a["id"] == "mia"
    assert client.post(f"{API}/agents", json={"name": "mia"}).status_code == 400
    ev = chat(client, agent_id="mia", text="hi")
    cid = ev[0]["chat"]["id"]
    assert client.delete(f"{API}/agents/mia").json()["ok"]
    assert client.get(f"{API}/chats/{cid}").json()["agent_id"] == "ida"
    assert client.delete(f"{API}/agents/ida").status_code == 400


def test_trigger_routine_with_mcp_connector(client, server):
    # a tiny MCP server inside the app is not available, so use the internal hub as "connector":
    # create a token by starting a chat is overkill – instead test validation + the trigger logic directly.
    from app.services import triggers
    t = {"mode": "new", "state": {}}
    ev = triggers.evaluate(t, json.dumps([{"id": "m1"}]))
    assert not ev["fire"]
    t["state"] = ev["state"]
    ev = triggers.evaluate(t, json.dumps({"mails": [{"id": "m1"}, {"id": "m2", "subject": "Neu"}]}))
    assert ev["fire"] and '"m2"' in ev["context"]
    r = client.post(f"{API}/routines", json={"name": "T", "prompt": "p", "trigger": {"connector": "nope", "tool": "x"}})
    assert r.status_code == 400 and "Connector" in r.json()["detail"]


def test_trigger_end_to_end_with_mock_mcp(client):
    """Connector -> tool list -> routine with trigger -> baseline -> new mail starts the routine with the new mail."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    mails = [{"id": "m1", "subject": "Hallo"}]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            m = json.loads(self.rfile.read(int(self.headers["content-length"])))
            if "id" not in m:
                self.send_response(202); self.end_headers(); return
            res = {"initialize": {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "mock"}},
                   "tools/list": {"tools": [{"name": "mails_suchen"}]},
                   "tools/call": {"content": [{"type": "text", "text": json.dumps(mails)}]}}[m["method"]]
            body = json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": res}).encode()
            self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("mcp-session-id", "s")
            self.end_headers(); self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = client.post(f"{API}/connectors", json={"name": "Mailbox", "url": f"http://127.0.0.1:{srv.server_port}/mcp?token=geheim1234"}).json()
        assert c["status"] == "ok" and c["tools"] == ["mails_suchen"] and "geheim" not in c["url"]
        t = client.post(f"{API}/routines/test-trigger", json={"trigger": {"connector": c["id"], "tool": "mails_suchen"}}).json()
        assert t["items"] == 1
        r = client.post(f"{API}/routines", json={"name": "Mails", "prompt": "Neue Mail bearbeiten", "agent_id": "lea",
                                                 "trigger": {"connector": c["id"], "tool": "mails_suchen", "every_sec": 30}}).json()
        assert r["trigger"]["every_sec"] == 30 and r["next_run"] is None
        assert "Ausgangszustand" in client.post(f"{API}/routines/{r['id']}/check").json()["note"]
        assert not client.post(f"{API}/routines/{r['id']}/check").json()["started"]
        mails.append({"id": "m2", "subject": "Chef"})
        assert client.post(f"{API}/routines/{r['id']}/check").json()["started"]
        run = wait(lambda: client.get(f"{API}/routines/{r['id']}").json()["runs"])
        assert run[0]["via"] == "trigger" and '"m2"' in run[0]["text"] and '"m1"' not in run[0]["text"]

        # own code instead of the fixed check: calls the MCP tool itself (no AI) and decides with fire()
        code = ('mails = tool_json("Mailbox", "mails_suchen")\n'
                'neu = new(mails)\n'
                'if neu:\n    fire("neu: " + ", ".join(m["subject"] for m in neu))\n'
                'print("anzahl", len(mails))')
        t = client.post(f"{API}/routines/test-trigger", json={"trigger": {"type": "script", "code": code}}).json()
        assert t["script"] and t["fire"] and "neu: Hallo, Chef" in t["info"] and "anzahl 2" in t["log"] and not t["error"]
        bad = client.post(f"{API}/routines", json={"name": "S", "prompt": "p", "trigger": {"type": "script", "code": "if x\n"}})
        assert bad.status_code == 400 and "Zeile 1" in bad.json()["detail"]
        r = client.post(f"{API}/routines", json={"name": "Skript", "prompt": "Mail bearbeiten", "agent_id": "lea",
                                                 "trigger": {"type": "script", "code": code, "every_sec": 60}}).json()
        assert r["trigger"]["type"] == "script"
        assert not client.post(f"{API}/routines/{r['id']}/check").json()["started"]  # first check only remembers
        mails.append({"id": "m3", "subject": "Dritte"})
        assert client.post(f"{API}/routines/{r['id']}/check").json()["started"]
        run = wait(lambda: client.get(f"{API}/routines/{r['id']}").json()["runs"])
        assert "neu: Dritte" in run[0]["text"] and "Chef" not in run[0]["text"]
        # errors in the code are reported with the line
        client.patch(f"{API}/routines/{r['id']}", json={"trigger": {"type": "script", "code": "x = 1\ntool('gibtsnicht', 'a')"}})
        res = client.post(f"{API}/routines/{r['id']}/check").json()
        assert not res["ok"] and "gibtsnicht" in res["error"] and "Zeile 2" in res["error"]
        # tasks have no length limit any more
        long = client.post(f"{API}/routines", json={"name": "Lang", "prompt": "x" * 20000, "interval_min": 60}).json()
        assert len(long["prompt"]) == 20000
    finally:
        srv.shutdown()


def _ws(server, client, path):
    from websockets.sync.client import connect
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    return connect(server.replace("http", "ws") + path, additional_headers={"Cookie": cookie} if cookie else {}, open_timeout=10)


def test_terminal_refuses_other_sites(server):
    import websockets
    from websockets.sync.client import connect
    try:  # a page from another site may not open the terminal (rejected in the handshake, HTTP 403)
        with connect(server.replace("http", "ws") + f"{API}/accounts/a1/terminal", origin="https://evil.example", open_timeout=10):
            raise AssertionError("connection must be refused")
    except websockets.exceptions.InvalidStatus as e:
        assert e.response.status_code == 403


@pytest.mark.skipif(sys.platform == "win32", reason="PTY only on Linux (Docker/CI)")
def test_terminal_login_like_t3code(server, client):
    """`claude auth login` in a real PTY: the browser sends the code, the CLI stores the login; reconnect replays output."""
    a = client.post(f"{API}/accounts", json={"name": "Terminal"}).json()
    path = f"{API}/accounts/{a['id']}/terminal?cmd=login&cols=100&rows=30"
    with _ws(server, client, path) as ws:
        out = ""
        while "Paste code" not in out:
            out += json.loads(ws.recv(timeout=10)).get("d", "")
        assert "oauth/authorize" in out
    # socket closed (e.g. phone switched to Claude) – the CLI keeps waiting; reconnect gets the buffer
    with _ws(server, client, path) as ws:
        assert "oauth/authorize" in json.loads(ws.recv(timeout=10))["d"]
        ws.send(json.dumps({"t": "in", "d": "goodcode1234#abc\r"}))
        while True:
            m = json.loads(ws.recv(timeout=15))
            if m["t"] == "exit":
                break
        assert m["code"] == 0 and m["logged_in"] is True
    assert next(x for x in client.get(f"{API}/accounts").json() if x["id"] == a["id"])["logged_in"]


def test_schedule_fixed_times():
    from datetime import datetime, timezone

    from app.services import schedule
    from app.services.errors import Invalid

    assert schedule.clean_times(["7:05", "18:30", "07:05"]) == ["07:05", "18:30"]
    assert schedule.clean_times("08:00, 12:00") == ["08:00", "12:00"]
    for bad in (["25:00"], ["7"], []):
        try:
            schedule.clean_times(bad)
            raise AssertionError(bad)
        except Invalid:
            pass
    # Saturday 24.10.2026 10:00 Berlin (CEST, UTC+2) -> next 18:30 the same day
    sat = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
    assert schedule.next_run(["07:00", "18:30"], None, sat) == datetime(2026, 10, 24, 16, 30, tzinfo=timezone.utc)
    # weekdays only -> Monday 07:00; after the switch to winter time (CET, UTC+1) on 25.10.
    assert schedule.next_run(["07:00"], [0, 1, 2, 3, 4], sat) == datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)
    assert schedule.describe(["07:00"], [0, 1, 2, 3, 4]) == "Mo–Fr 07:00"
    assert schedule.describe(["07:00", "18:30"], None) == "täglich 07:00, 18:30"


def test_routine_with_fixed_times(client):
    r = client.post(f"{API}/routines", json={"name": "Morgens", "prompt": "Guten Morgen", "times": ["07:00"], "days": [0, 1, 2, 3, 4]}).json()
    assert r["times"] == ["07:00"] and r["days"] == [0, 1, 2, 3, 4] and r["next_run"] and r["trigger"] is None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    nxt = datetime.fromisoformat(r["next_run"]).astimezone(ZoneInfo("Europe/Berlin"))
    assert (nxt.hour, nxt.minute) == (7, 0) and nxt.weekday() < 5
    # back to an interval
    r2 = client.patch(f"{API}/routines/{r['id']}", json={"times": None, "interval_min": 30}).json()
    assert r2["times"] is None and r2["interval_min"] == 30
    assert client.post(f"{API}/routines", json={"name": "X", "prompt": "p", "times": ["99:00"]}).status_code == 400
    client.delete(f"{API}/routines/{r['id']}")


def test_one_time_routine_deletes_itself(client):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    when = (datetime.now(ZoneInfo("Europe/Berlin")) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    r = client.post(f"{API}/routines", json={"name": "Morgen", "prompt": "Erinner mich", "run_at": when.strftime("%Y-%m-%dT%H:%M")}).json()
    assert r["run_at"] and r["next_run"] == r["run_at"]
    assert datetime.fromisoformat(r["next_run"]).astimezone(ZoneInfo("Europe/Berlin")).hour == 9
    assert client.post(f"{API}/routines", json={"name": "Alt", "prompt": "p", "run_at": "2020-01-01T09:00"}).status_code == 400
    # after a successful run it is gone
    client.post(f"{API}/routines/{r['id']}/run")
    wait(lambda: client.get(f"{API}/routines/{r['id']}").status_code == 404)
    # a failed one stays, paused, with its run in the history
    client.patch(f"{API}/agents/lea", json={"enabled": False})
    f = client.post(f"{API}/routines", json={"name": "Fail", "prompt": "p", "agent_id": "lea", "run_at": when.strftime("%Y-%m-%dT%H:%M")}).json()
    client.post(f"{API}/routines/{f['id']}/run")
    runs = wait(lambda: client.get(f"{API}/routines/{f['id']}").json()["runs"])
    after = client.get(f"{API}/routines/{f['id']}").json()
    assert not runs[0]["ok"] and after["enabled"] is False and after["next_run"] is None
    client.patch(f"{API}/agents/lea", json={"enabled": True})
    # Ida can set one up from the chat ("mach das morgen um 9")
    import json
    ev = chat(client, text="TOOL routine_create " + json.dumps({"name": "Morgen 9", "prompt": "Erinnern", "run_at": when.strftime("%Y-%m-%dT%H:%M")}))
    out = "".join(e.get("text", "") for e in ev)
    assert "einmalig am" in out and "automatisch gelöscht" in out
    # agents get the current time so "tomorrow" works
    from app.services.claude.prompts import now_line
    assert now_line().startswith("Jetzt ist ")
