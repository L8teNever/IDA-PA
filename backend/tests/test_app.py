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
    for url in ("/", "/chats", "/einstellungen/agents/lea", "/routinen/neu", "/tracker/neu", "/tracker/x1", "/tracker/x1/timer", "/erinnerungen/x1"):
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


def test_agent_profile_picture(client):
    gif = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"
    assert client.post(f"{API}/agents", json={"name": "Bild"}).json()["avatar"] is None
    up = lambda name, data: client.post(f"{API}/agents/bild/avatar", files={"file": (name, data, "application/octet-stream")})  # noqa: E731
    assert up("x.txt", b"hallo").status_code == 400  # only pictures
    assert up("x.gif", b"").status_code == 400
    assert up("x.gif", gif + b"0" * (2 * 1024 * 1024)).status_code == 400  # too big
    a = up("x.gif", gif).json()
    assert a["avatar"].startswith(f"{API}/agents/bild/avatar?v=")
    assert [x for x in client.get(f"{API}/agents").json() if x["id"] == "bild"][0]["avatar"] == a["avatar"]  # also in the lists
    r = client.get(a["avatar"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/gif" and r.content == gif
    assert up("y.gif", gif).json()["avatar"] != a["avatar"]  # new picture = new address (no stale cache)
    assert client.delete(f"{API}/agents/bild/avatar").json()["avatar"] is None
    assert client.get(f"{API}/agents/bild/avatar").status_code == 404
    up("x.gif", gif)
    client.delete(f"{API}/agents/bild")
    assert client.get(f"{API}/agents/bild/avatar").status_code == 404


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


def test_on_demand_routine_runs_only_when_called(client):
    import json
    r = client.post(f"{API}/routines", json={"name": "Abruf", "prompt": "Mach den Ablauf", "on_demand": True}).json()
    assert r["on_demand"] and r["next_run"] is None and r["trigger"] is None and not r["times"]
    # an agent starts it via MCP, with a note for this run
    ev = chat(client, text="TOOL routine_run_now " + json.dumps({"id": r["id"], "info": "Paket ist da"}))
    assert '"ok": true' in "".join(e.get("text", "") for e in ev)
    run = wait(lambda: client.get(f"{API}/routines/{r['id']}").json()["runs"])
    assert run[0]["via"] == "manual" and "Paket ist da" in run[0]["text"] and "Auslöser" not in run[0]["text"]
    assert client.get(f"{API}/routines/{r['id']}").json()["next_run"] is None  # still no schedule after the run
    # back to an interval
    r2 = client.patch(f"{API}/routines/{r['id']}", json={"interval_min": 30}).json()
    assert not r2["on_demand"] and r2["next_run"]
    assert client.post(f"{API}/routines", json={"name": "X", "prompt": "p", "on_demand": True, "times": ["07:00"]}).status_code == 400


def test_reminders_are_one_time_and_not_routines(client):
    """„Erinnere mich morgen um 9“ is a reminder (once), not a routine (repeats)."""
    import json
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    when = (datetime.now(ZoneInfo("Europe/Berlin")) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    w = when.strftime("%Y-%m-%dT%H:%M")
    # routines refuse one-time things and point to reminders
    bad = client.post(f"{API}/routines", json={"name": "Morgen", "prompt": "Erinner mich", "run_at": w})
    assert bad.status_code == 400 and "Erinnerung" in bad.json()["detail"]
    # plain reminder: no AI, just a notification
    r = client.post(f"{API}/reminders", json={"text": "Müll rausbringen", "when": w}).json()
    assert r["kind"] == "remind" and r["status"] == "pending"
    assert datetime.fromisoformat(r["due_at"]).astimezone(ZoneInfo("Europe/Berlin")).hour == 9
    assert client.post(f"{API}/reminders", json={"text": "x", "when": "2020-01-01T09:00"}).status_code == 400
    before = len(client.get(f"{API}/messages").json())
    client.post(f"{API}/reminders/{r['id']}/fire")
    done = wait(lambda: next(x for x in client.get(f"{API}/reminders").json() if x["id"] == r["id"])["status"] == "done")
    assert done
    msgs = client.get(f"{API}/messages").json()
    assert len(msgs) == before + 1 and any("Müll rausbringen" in m["text"] for m in msgs)
    # task: the agent does it at that time, the result arrives as a notification
    t = client.post(f"{API}/reminders", json={"text": "Fasse den Tag zusammen", "when": w, "kind": "task", "agent_id": "lea"}).json()
    client.post(f"{API}/reminders/{t['id']}/fire")
    wait(lambda: next(x for x in client.get(f"{API}/reminders").json() if x["id"] == t["id"])["status"] == "done")
    t = next(x for x in client.get(f"{API}/reminders").json() if x["id"] == t["id"])
    assert "Fasse den Tag zusammen" in t["result"]
    # Ida sets one up from the chat; the prompt tells her the difference
    ev = chat(client, text="TOOL reminder_create " + json.dumps({"text": "Zahnarzt anrufen", "when": w}))
    out = "".join(e.get("text", "") for e in ev)
    assert '"ok": true' in out and "nur Erinnerung" in out
    assert any(x["text"] == "Zahnarzt anrufen" for x in client.get(f"{API}/reminders").json())
    from app.services.claude.prompts import now_line
    assert now_line().startswith("Jetzt ist ")
    for x in client.get(f"{API}/reminders").json():
        client.delete(f"{API}/reminders/{x['id']}")
    assert client.get(f"{API}/reminders").json() == []


def test_day_plan_shows_marked_items_and_todays_calendar(client):
    """Start page: only routines/reminders marked for it (by the user or the AI) plus today's events from a calendar MCP – no AI involved."""
    import threading
    from datetime import datetime, timedelta
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    today, tomorrow = now.date(), now.date() + timedelta(days=1)
    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            m = json.loads(self.rfile.read(int(self.headers["content-length"])))
            if "id" not in m:
                self.send_response(202); self.end_headers(); return
            if m["method"] == "tools/call":
                calls.append(m["params"]["arguments"])
            events = [{"titel": "Zahnarzt", "start": f"{today}T23:58:00", "ende": f"{today}T23:59:00", "ort": "Praxis"},
                      {"titel": "Feiertag", "start": str(today), "ende": str(tomorrow)},
                      {"titel": "Morgen-Termin", "start": f"{tomorrow}T10:00:00"}]
            res = {"initialize": {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "cal"}},
                   "tools/list": {"tools": [{"name": "google_termine_liste", "inputSchema": {"properties": {"von": {"type": "string"}, "bis": {"type": "string"}}}}]},
                   "tools/call": {"content": [{"type": "text", "text": "\n".join(json.dumps(e) for e in events)}]}}[m["method"]]
            body = json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": res}).encode()
            self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(body)

    titles = lambda: [x["title"] for x in client.get(f"{API}/home").json()["timeline"]]  # noqa: E731
    shown = client.post(f"{API}/routines", json={"name": "Sichtbar", "prompt": "p", "interval_min": 1, "show_on_home": True}).json()
    hidden = client.post(f"{API}/routines", json={"name": "Hintergrund", "prompt": "p", "interval_min": 1}).json()
    assert shown["show_on_home"] and not hidden["show_on_home"]  # routines stay out unless marked
    soon = (now + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M")
    rem = client.post(f"{API}/reminders", json={"text": "Wäsche", "when": soon}).json()
    quiet = client.post(f"{API}/reminders", json={"text": "Leise", "when": soon, "show_on_home": False}).json()
    assert rem["show_on_home"] and not quiet["show_on_home"]  # reminders show by default
    # the AI sets the mark itself when creating
    ev = chat(client, text="TOOL routine_create " + json.dumps({"name": "Von Ida", "prompt": "p", "interval_min": 1, "show_on_home": True}))
    assert '"ok": true' in "".join(e.get("text", "") for e in ev)
    ida = next(r for r in client.get(f"{API}/routines").json() if r["name"] == "Von Ida")
    if (now + timedelta(minutes=3)).date() == today:
        t = titles()
        assert "Sichtbar" in t and "Von Ida" in t and "⏰ Wäsche" in t
        assert "Hintergrund" not in t and "⏰ Leise" not in t
        client.patch(f"{API}/routines/{shown['id']}", json={"show_on_home": False})
        assert "Sichtbar" not in titles()

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = client.post(f"{API}/connectors", json={"name": "Kalender", "url": f"http://127.0.0.1:{srv.server_port}/mcp"}).json()
        s = client.get(f"{API}/home-calendar").json()
        assert s["active"]["tool"] == "google_termine_liste" and s["active"]["auto"]  # found automatically
        home = client.get(f"{API}/home").json()
        t = [x["title"] for x in home["timeline"]]
        assert home["calendar"] == {"on": True, "error": None, "local": False}
        assert "Zahnarzt" in t and "Feiertag" in t and "Morgen-Termin" not in t
        assert next(x for x in home["timeline"] if x["title"] == "Feiertag")["all_day"]
        assert calls[-1]["von"].startswith(str(today)) and calls[-1]["bis"].startswith(str(tomorrow))  # only today is asked for
        assert client.get(f"{API}/home-calendar/suggest", params={"connector": c["id"], "tool": "google_termine_liste"}).json()["args"] == {"von": "{heute_start}", "bis": "{heute_ende}"}
        # fixed tool with own arguments
        client.put(f"{API}/home-calendar", json={"connector": c["id"], "tool": "google_termine_liste", "args": {"von": "{heute}"}})
        test = client.post(f"{API}/home-calendar/test", json={}).json()
        assert calls[-1] == {"von": str(today)} and [e["title"] for e in test["events"]] == ["Feiertag", "Zahnarzt"]
        # switched off: no calendar call at all
        client.put(f"{API}/home-calendar", json={"off": True})
        n = len(calls)
        assert client.get(f"{API}/home").json()["calendar"]["on"] is False and len(calls) == n
        client.put(f"{API}/home-calendar", json={})
        client.delete(f"{API}/connectors/{c['id']}")
    finally:
        srv.shutdown()
    for r in (shown, hidden, ida):
        client.delete(f"{API}/routines/{r['id']}")
    for r in (rem, quiet):
        client.delete(f"{API}/reminders/{r['id']}")


def test_antigravity_sign_in_models_chat_and_limits(client):
    """Like T3 Code: the ACP agent shows a Google link, the return address (127.0.0.1) is handed back to it."""
    from urllib.parse import parse_qs, urlparse
    a = client.post(f"{API}/accounts", json={"provider": "antigravity"}).json()
    assert a["provider"] == "antigravity" and not a["logged_in"]
    # a run without sign-in fails clearly
    ev = chat(client, text="Hallo", model="agy:gemini-3-flash", account_id=a["id"])
    assert not ev[-1]["done"]["ok"]
    # sign-in: Google link -> (Google) -> return address pasted into IDA
    st = client.post(f"{API}/accounts/{a['id']}/antigravity/login").json()
    assert st["phase"] == "waiting" and st["url"].startswith("https://accounts.google.com/")
    q = parse_qs(urlparse(st["url"]).query)
    redirect, state = q["redirect_uri"][0], q["state"][0]
    bad = client.post(f"{API}/accounts/{a['id']}/antigravity/callback", json={"url": f"{redirect}?code=x&state=falsch"})
    assert bad.status_code == 400
    done = client.post(f"{API}/accounts/{a['id']}/antigravity/callback", json={"url": f"{redirect}?code=abc&state={state}&scope=x"}).json()
    assert done["phase"] == "done" and [m["id"] for m in done["models"]][0] == "gemini-3.1-pro-high"
    assert client.get(f"{API}/accounts").json()[-1]["logged_in"]
    boot = client.get(f"{API}/bootstrap").json()
    assert "agy:gemini-3.1-pro-high" in boot["models"] and boot["model_names"]["agy:claude-sonnet-4-6"] == "Claude Sonnet 4.6 (Thinking)"
    # chat on the Antigravity account with the chosen model; instructions go in front of the message
    ev = chat(client, text="Hallo Gemini", model="agy:gemini-3.1-pro-high")
    text = "".join(e.get("text", "") for e in ev)
    assert text == "[acp|gemini-3.1-pro-high] Hallo Gemini (mit Anweisungen)" and ev[-1]["done"]["ok"]
    cid = ev[0]["chat"]["id"]
    # follow-up: fresh ACP session, the history is sent along; IDA's MCP tools work
    ev = chat(client, chat_id=cid, text='TOOL memory_save {"text": "Antigravity kann Tools", "type": "fact"}')
    assert '"ok": true' in "".join(e.get("text", "") for e in ev)
    assert any(e.get("tool", {}).get("name") == "memory_save" for e in ev) and any("tool_result" in e for e in ev)
    # Claude models still run on Claude accounts
    assert "".join(e.get("text", "") for e in chat(client, text="Hallo Claude")).startswith("[a1|claude-sonnet-5-5]")
    # agents accept agy models; limits are shown per account (the fake login cannot reach Google)
    assert client.patch(f"{API}/agents/lea", json={"model": "agy:gemini-3-flash"}).json()["model"] == "agy:gemini-3-flash"
    client.patch(f"{API}/agents/lea", json={"model": "claude-sonnet-5-5"})
    u = next(x for x in client.get(f"{API}/usage").json() if x["id"] == a["id"])
    assert u["provider"] == "antigravity" and ("windows" in u or "error" in u)
    # several accounts of the same provider are possible
    b = client.post(f"{API}/accounts", json={"provider": "antigravity"}).json()
    assert b["id"] != a["id"]
    client.delete(f"{API}/accounts/{b['id']}")
    # sign out
    client.delete(f"{API}/accounts/{a['id']}/login")
    assert not next(x for x in client.get(f"{API}/accounts").json() if x["id"] == a["id"])["logged_in"]
    client.delete(f"{API}/accounts/{a['id']}")


def test_transcribe_microphone_input(client, monkeypatch):
    """The recording goes to the (local) Whisper model; here the model is replaced, the real one needs a download."""
    from app.services import speech
    seen = {}

    def fake(path):
        seen["size"] = __import__("os").path.getsize(path)
        seen["suffix"] = path[-5:]
        return {"text": "Erinnere mich morgen an den Müll", "seconds": 2.4, "language": "de"}
    monkeypatch.setattr(speech, "transcribe_file", fake)
    r = client.post(f"{API}/transcribe", files={"audio": ("aufnahme.webm", b"\x1aE\xdf\xa3" + b"0" * 2000, "audio/webm")}).json()
    assert r["text"] == "Erinnere mich morgen an den Müll" and seen == {"size": 2004, "suffix": ".webm"}
    bad = client.post(f"{API}/transcribe", files={"audio": ("leer.webm", b"", "audio/webm")})
    assert bad.status_code == 400


def test_audio_decoding_really_works(tmp_path):
    # faster-whisper decodes with PyAV; an incompatible PyAV version breaks every recording
    import wave

    from faster_whisper.audio import decode_audio
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 8000)
    assert len(decode_audio(str(p))) == 8000


def test_chat_effort_reaches_claude(client):
    """The thinking level picked in the model menu is passed to the CLI as --effort."""
    ev = chat(client, text="Hallo", effort="max")
    out = "".join(e.get("text", "") for e in ev)
    assert "|max]" in out
    ev = chat(client, text="Hallo")
    assert "|max]" not in "".join(e.get("text", "") for e in ev)
    r = client.post(f"{API}/chat", json={"text": "x", "effort": "gigantisch"})
    assert r.status_code == 422
    for c in client.get(f"{API}/chats").json():
        client.delete(f"{API}/chats/{c['id']}")


def test_stop_button_ends_running_answer(client):
    """While the agent is thinking, the stop button ends the run right away; the chat stays usable."""
    import threading
    import time as _t
    box = {}

    def run():
        box["ev"] = chat(client, text="SLOW denk lange nach")
    th = threading.Thread(target=run)
    t0 = _t.time()
    th.start()
    cid = wait(lambda: next((c["id"] for c in client.get(f"{API}/chats").json()), None))
    wait(lambda: client.post(f"{API}/chats/{cid}/stop").json()["ok"])
    th.join(10)
    assert not th.is_alive() and _t.time() - t0 < 4
    done = next(e["done"] for e in box["ev"] if "done" in e)
    assert done["stopped"] and not done["ok"]
    msgs = client.get(f"{API}/chats/{cid}").json()["messages"]
    assert msgs[-1]["meta"].get("stopped")
    ev = chat(client, chat_id=cid, text="weiter")
    assert next(e["done"] for e in ev if "done" in e)["ok"]
    assert client.post(f"{API}/chats/{cid}/stop").json()["ok"] is False
    client.delete(f"{API}/chats/{cid}")


def test_trackers(client):
    """Daily things to tick off – by the user or an agent; open slots are followed up (notification or routine)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Europe/Berlin")).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    bad = client.post(f"{API}/trackers", json={"name": "X", "slots": [{"label": "M", "time": "25:00"}]})
    assert bad.status_code == 400
    assert client.post(f"{API}/trackers", json={"name": "X", "on_missed": "routine"}).status_code == 400  # needs a routine
    assert client.post(f"{API}/trackers", json={"name": "X", "on_missed": "notify"}).status_code == 400  # needs a time
    t = client.post(f"{API}/trackers", json={"name": "Tablette", "slots": [{"label": "Morgens", "time": "08:00"}, {"label": "Abends", "time": "20:00"}],
                                             "on_missed": "notify"}).json()
    assert [s["label"] for s in t["slots"]] == ["Morgens", "Abends"] and t["today"]["done"] == 0 and len(t["recent"]) == 14
    morning, evening = (s["key"] for s in t["slots"])
    # tick, untick, tick the first open one
    t = client.post(f"{API}/trackers/{t['id']}/check", json={"slot": morning}).json()
    assert t["today"]["slots"][0]["done"] and t["today"]["slots"][0]["by"] == "user"
    t = client.post(f"{API}/trackers/{t['id']}/check", json={"slot": morning, "undo": True}).json()
    assert t["today"]["done"] == 0
    t = client.post(f"{API}/trackers/{t['id']}/check", json={}).json()
    # without slot it takes the slot that fits the time of day: from 18:00 on the evening (due within 2 h)
    first = 1 if datetime.now(ZoneInfo("Europe/Berlin")).hour >= 18 else 0
    assert t["today"]["slots"][first]["done"] and not t["today"]["slots"][1 - first]["done"]
    if first:  # the rest expects the morning ticked and the evening open
        client.post(f"{API}/trackers/{t['id']}/check", json={"slot": evening, "undo": True})
        t = client.post(f"{API}/trackers/{t['id']}/check", json={"slot": morning}).json()
    # add a forgotten day; the streak counts it, an open today does not break it
    for s in (morning, evening):
        t = client.post(f"{API}/trackers/{t['id']}/check", json={"slot": s, "day": yesterday}).json()
    assert t["streak"] == 1 and t["recent"][-2]["done"] == 2
    t = client.post(f"{API}/trackers/{t['id']}/check", json={"slot": evening}).json()
    assert t["streak"] == 2 and t["today"]["done"] == 2
    assert client.post(f"{API}/trackers/{t['id']}/check", json={"day": (today + timedelta(days=1)).isoformat()}).status_code == 400
    # renaming a slot keeps its ticks
    t = client.patch(f"{API}/trackers/{t['id']}", json={"slots": [{"key": morning, "label": "Früh", "time": "07:30"}, {"key": evening, "label": "Abends", "time": "20:00"}]}).json()
    assert t["today"]["slots"][0]["label"] == "Früh" and t["today"]["slots"][0]["done"]
    # follow-up: notification without AI
    before = len(client.get(f"{API}/messages").json())
    assert client.post(f"{API}/trackers/{t['id']}/missed/{morning}").json()["action"] == "notify"
    msgs = client.get(f"{API}/messages").json()
    assert len(msgs) == before + 1 and any("Tablette" in m["title"] and "Früh" in m["text"] for m in msgs)
    # follow-up: a linked routine (on demand) runs and learns which slot is open
    r = client.post(f"{API}/routines", json={"name": "Tabletten-Nachhaken", "prompt": "Frag nach der Tablette", "on_demand": True}).json()
    t = client.patch(f"{API}/trackers/{t['id']}", json={"on_missed": "routine", "routine_id": r["id"]}).json()
    assert client.post(f"{API}/trackers/{t['id']}/missed/{evening}").json()["action"] == "routine"
    run = wait(lambda: client.get(f"{API}/routines/{r['id']}").json()["runs"])
    assert run[0]["via"] == "tracker" and "Abends (20:00)" in run[0]["text"] and "tracker_check" in run[0]["text"]
    assert client.get(f"{API}/routines/{r['id']}").json()["trackers"] == [{"id": t["id"], "name": "Tablette"}]
    # day plan: both slots, ticked
    plan = [x for x in client.get(f"{API}/home").json()["timeline"] if x["kind"] == "tracker" and x["tracker_id"] == t["id"]]
    assert len(plan) == 2 and all(x["done"] for x in plan) and plan[0]["title"] == "Tablette · Früh"
    # agents: create and tick via MCP; their name is kept as who ticked it
    ev = chat(client, text="TOOL tracker_create " + json.dumps({"name": "Vitamin D", "slots": [{"time": "09:00"}], "on_missed": "notify"}))
    assert '"ok": true' in "".join(e.get("text", "") for e in ev)
    vit = next(x for x in client.get(f"{API}/trackers").json() if x["name"] == "Vitamin D")
    assert vit["slots"][0]["label"] == "Morgens" and vit["created_by"] == "ida"
    ev = chat(client, text="TOOL tracker_check " + json.dumps({"id": vit["id"], "slot": "morgens"}))
    assert '"abgehakt": true' in "".join(e.get("text", "") for e in ev)
    vit = next(x for x in client.get(f"{API}/trackers").json() if x["id"] == vit["id"])
    assert vit["today"]["slots"][0]["by"] == "ida"
    ev = chat(client, text="TOOL tracker_list {}")
    assert "Vitamin D" in "".join(e.get("text", "") for e in ev)
    # deleting the routine unlinks it; the follow-up falls back to a notification
    client.delete(f"{API}/routines/{r['id']}")
    assert client.post(f"{API}/trackers/{t['id']}/missed/{evening}").json()["action"] == "notify"
    for x in client.get(f"{API}/trackers").json():
        assert client.delete(f"{API}/trackers/{x['id']}").status_code == 200
    assert client.get(f"{API}/trackers").json() == []


def test_tracker_with_duration(client):
    """Brush teeth 3 min: a tracker with a duration (timer before ticking); 'next' tells what would be ticked now."""
    for bad in (5, 7201):
        assert client.post(f"{API}/trackers", json={"name": "X", "duration_s": bad}).status_code == 400
    t = client.post(f"{API}/trackers", json={"name": "Zähne", "slots": [{"time": "07:00"}, {"time": "20:00"}], "duration_s": 180}).json()
    assert t["duration_s"] == 180 and t["next"] in [s["key"] for s in t["slots"]]
    assert client.patch(f"{API}/trackers/{t['id']}", json={"duration_s": 15}).json()["duration_s"] == 15
    nxt = t["next"]
    r = client.post(f"{API}/trackers/{t['id']}/check", json={}).json()
    assert r["ticked"]["status"] == "checked" and r["ticked"]["slot"]["key"] == nxt and r["next"] != nxt
    plan = [x for x in client.get(f"{API}/home").json()["timeline"] if x["kind"] == "tracker" and x["tracker_id"] == t["id"]]
    assert plan and all(x["duration_s"] == 15 for x in plan)
    assert client.patch(f"{API}/trackers/{t['id']}", json={"duration_s": 0}).json()["duration_s"] == 0
    client.delete(f"{API}/trackers/{t['id']}")
    # agents set the duration, too
    ev = chat(client, text="TOOL tracker_create " + json.dumps({"name": "Zähne putzen", "slots": [{"time": "07:00"}, {"time": "20:00"}], "duration_s": 180}))
    assert '"ok": true' in "".join(e.get("text", "") for e in ev)
    z = next(x for x in client.get(f"{API}/trackers").json() if x["name"] == "Zähne putzen")
    assert z["duration_s"] == 180 and len(z["slots"]) == 2
    assert "180" in "".join(e.get("text", "") for e in chat(client, text="TOOL tracker_list {}"))
    client.delete(f"{API}/trackers/{z['id']}")


def test_tracker_follow_up_timing(client):
    """A slot is followed up once its time is reached, at most 3 h later, once per day and only while open."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.models import Tracker
    from app.services.trackers import due_slots
    z = ZoneInfo("Europe/Berlin")
    t = Tracker(name="T", enabled=True, on_missed="notify", days=None, fired={},
                slots=[{"key": "m", "label": "Morgens", "time": "08:00"}, {"key": "a", "label": "Abends", "time": "20:00"}, {"key": "x", "label": "Egal", "time": None}])
    at = lambda h, m=0: datetime(2026, 10, 26, h, m, tzinfo=z)  # noqa: E731 – a Monday
    keys = lambda now, done=(): [s["key"] for s in due_slots(t, now, set(done))]  # noqa: E731
    assert keys(at(7, 59)) == []
    assert keys(at(8, 0)) == ["m"] and keys(at(10, 59)) == ["m"]
    assert keys(at(11, 1)) == []  # too late (e.g. after a restart) – not followed up any more
    assert keys(at(8, 30), done=["m"]) == []  # ticked: nothing
    t.fired = {"m": "2026-10-26"}
    assert keys(at(9)) == [] and keys(at(20, 5)) == ["a"]  # once per day and slot; evening separately
    t.days = [5, 6]
    assert keys(at(20, 5)) == []  # not due on Mondays
    t.days, t.on_missed = None, "none"
    assert keys(at(20, 5)) == []


def test_tracker_pick_slot_by_time_of_day(client):
    """A tick without slot (NFC link, „hab ich gemacht“) takes the slot that fits the time of day."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.models import Tracker
    from app.services.trackers import _clean_slots, pick_slot
    z = ZoneInfo("Europe/Berlin")
    at = lambda h, m=0: datetime(2026, 10, 26, h, m, tzinfo=z)  # noqa: E731
    t = Tracker(name="Tablette", slots=[{"key": "m", "label": "Morgens", "time": "08:00"}, {"key": "a", "label": "Abends", "time": "20:00"}])
    key = lambda now, done=(): (pick_slot(t, now, set(done)) or {}).get("key")  # noqa: E731
    assert key(at(7, 30)) == "m"  # shortly before: the morning
    assert key(at(6, 30)) == "m"  # nothing due yet: the earliest open one (taken early)
    assert key(at(14)) == "m"  # morning still open: catch up on it
    assert key(at(14), done=["m"]) == "a"  # morning done: the evening (early)
    assert key(at(21)) == "a"  # evening: the evening one – the morning stays missed
    assert key(at(21), done=["m", "a"]) is None
    # 3× a day, no matter when: one after the other
    slots = _clean_slots([{}, {}, {}], [])
    assert [s["label"] for s in slots] == ["1. Mal", "2. Mal", "3. Mal"]
    t3 = Tracker(name="Wasser", slots=slots)
    done = []
    for _ in range(3):
        done.append(pick_slot(t3, at(12), set(done))["key"])
    assert done == [s["key"] for s in slots] and pick_slot(t3, at(12), set(done)) is None


def test_tracker_links_tick_by_tapping(client):
    """Secret links (NFC tags): opening one ticks the tracker once – the slot that fits now, or a fixed one."""
    from datetime import timedelta

    from app.services import trackers as svc
    t = client.post(f"{API}/trackers", json={"name": "Wasser", "slots": [{}, {}]}).json()
    first, second = (s["key"] for s in t["slots"])
    a = client.post(f"{API}/trackers/{t['id']}/links", json={"name": "Küche"}).json()
    b = client.post(f"{API}/trackers/{t['id']}/links", json={"name": "Bad", "slot": second}).json()
    assert a["path"] == f"/t/{a['token']}" and a["slot"] is None and b["slot"] == second and len(a["token"]) >= 20
    assert [x["name"] for x in client.get(f"{API}/trackers/{t['id']}/links").json()] == ["Küche", "Bad"]
    assert next(x for x in client.get(f"{API}/trackers").json() if x["id"] == t["id"])["links"] == 2
    assert client.get(f"/t/{a['token']}").status_code == 200  # the page is the app (it ticks via POST, not on load)

    r = client.post(f"{API}/tap/{a['token']}").json()
    assert r["status"] == "checked" and r["slot"]["key"] == first and r["link"] == "Küche"
    assert r["tracker"]["today"]["slots"][0]["by"] == "link"
    assert client.post(f"{API}/tap/{a['token']}").json()["status"] == "repeat"  # tag read twice: only once
    assert client.post(f"{API}/tap/{b['token']}").json()["status"] == "checked"  # another link: fixed slot
    svc.REPEAT = timedelta(0)  # the server runs in this process: switch off the 30-s guard against double reads
    try:
        assert client.post(f"{API}/tap/{b['token']}").json()["status"] == "already"
        r = client.post(f"{API}/tap/{a['token']}").json()
    finally:
        svc.REPEAT = timedelta(seconds=30)
    assert r["status"] == "all_done" and r["tracker"]["today"]["done"] == 2
    assert [x["uses"] for x in client.get(f"{API}/trackers/{t['id']}/links").json()] == [1, 1]
    assert client.delete(f"{API}/trackers/{t['id']}/links/{a['id']}").status_code == 200
    assert client.post(f"{API}/tap/{a['token']}").status_code == 404
    client.delete(f"{API}/trackers/{t['id']}")
    assert client.post(f"{API}/tap/{b['token']}").status_code == 404  # links go with their tracker


def test_chat_files_and_pictures(client):
    """Pictures and PDFs go to the model as they are, text files as text; other kinds are refused."""
    import struct
    import zlib

    def png():
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"\x00" + b"\xff\x00\x00" * 2) * 2) + chunk(b"IEND", b""))
    up = lambda name, data, mime="application/octet-stream": client.post(f"{API}/uploads", files={"file": (name, data, mime)})  # noqa: E731
    img = up("foto.png", png(), "image/png").json()
    pdf = up("brief.pdf", b"%PDF-1.4\n%fake\n").json()
    txt = up("liste.txt", "Milch, Eier, Käse".encode(), "text/plain").json()
    assert (img["kind"], pdf["kind"], txt["kind"]) == ("image", "pdf", "text")
    assert up("doc.docx", b"PK\x03\x04 word").status_code == 400  # Word etc. cannot be read by the model
    assert up("leer.txt", b"").status_code == 400
    gone = up("weg.txt", b"x").json()
    assert client.delete(f"{API}/uploads/{gone['id']}").status_code == 200
    assert client.get(f"{API}/files/{gone['id']}").status_code == 404
    r = client.get(f"{API}/files/{img['id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    ev = chat(client, text="Was siehst du?", files=[img["id"], pdf["id"], txt["id"]])
    out = "".join(e.get("text", "") for e in ev)
    assert "[BILDER:1 PDFS:1]" in out and "Milch, Eier, Käse" in out and '<datei name="liste.txt">' in out
    cid = ev[0]["chat"]["id"]
    user = client.get(f"{API}/chats/{cid}").json()["messages"][0]
    assert user["text"] == "Was siehst du?" and [f["name"] for f in user["meta"]["files"]] == ["foto.png", "brief.pdf", "liste.txt"]
    # only files: the chat is named after the first one
    pic = up("katze.png", png(), "image/png").json()
    ev = chat(client, files=[pic["id"]])
    assert ev[0]["chat"]["title"] == "katze.png" and "[BILDER:1 PDFS:0]" in "".join(e.get("text", "") for e in ev)
    assert client.post(f"{API}/chat", json={"text": "", "files": []}).status_code == 400
    assert client.post(f"{API}/chat", json={"text": "x", "files": [img["id"]], "chat_id": ev[0]["chat"]["id"]}).status_code == 400  # belongs to another chat
    client.delete(f"{API}/chats/{cid}")
    assert client.get(f"{API}/files/{img['id']}").status_code == 404


def test_chat_answer_goes_on_when_the_page_is_closed(client):
    """The answer runs on the server: leaving the page does not stop it, coming back shows it (live, then saved)."""
    with client.stream("POST", f"{API}/chat", json={"text": "SLOW Erzaehl was"}) as r:
        first = json.loads(next(r.iter_lines()))
    cid = first["chat"]["id"]  # the browser went away right after the start
    c = client.get(f"{API}/chats/{cid}").json()
    assert c["running"] and [m["role"] for m in c["messages"]] == ["user"] and c["running_since"]
    assert next(x for x in client.get(f"{API}/chats").json() if x["id"] == cid)["running"]
    assert client.post(f"{API}/chat", json={"chat_id": cid, "text": "noch was"}).status_code == 400  # one answer at a time
    # coming back: the answer so far is replayed and keeps streaming until it is done
    with client.stream("GET", f"{API}/chats/{cid}/live") as r:
        evs = [json.loads(x) for x in r.iter_lines() if x]
    assert evs[-1]["done"]["ok"] and "SLOW Erzaehl was" in "".join(e.get("text", "") for e in evs)
    c = client.get(f"{API}/chats/{cid}").json()
    assert not c["running"] and c["messages"][-1]["role"] == "assistant" and "Erzaehl was" in c["messages"][-1]["text"]
    assert c["running_since"] is None and c["messages"][-1]["meta"]["ms"] == evs[-1]["done"]["ms"] >= 0  # how long it took
    # just after the end the run can still be followed (a browser coming back gets "done")
    with client.stream("GET", f"{API}/chats/{cid}/live?after=1") as r:
        assert json.loads(list(r.iter_lines())[-1])["done"]["ok"]
    assert client.get(f"{API}/chats/gibtsnicht/live").status_code == 404


def test_kalender_endpoints_need_the_token(client):
    # ohne KALENDER_IDA_TOKEN (Testumgebung) nimmt IDA nichts vom Kalender an
    assert client.post(f"{API}/ext/kalender", json={"type": "alarm"}).status_code == 401
    assert client.post(f"{API}/ext/kalender/ask", json={"text": "Hallo"}, headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert client.get("/kal/api/v1/calendars").status_code == 503  # KALENDER_URL fehlt
