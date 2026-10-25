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
