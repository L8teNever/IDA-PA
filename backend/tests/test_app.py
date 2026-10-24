import json

import httpx

from .conftest import chat, wait

API = "/api/v1"


def test_login_required(server):
    with httpx.Client(base_url=server) as c:
        assert c.get(f"{API}/bootstrap").status_code == 401
        r = c.get("/einstellungen/agents", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert c.post(f"{API}/auth/login", json={"password": "falsch"}).status_code == 401


def test_pages_get_the_app(client):
    for url in ("/", "/chats", "/einstellungen/agents/lea", "/routinen/neu"):
        r = client.get(url)
        assert r.status_code == 200 and "/static/js/main.js" in r.text
    assert client.get("/api/v1/gibtsnicht").status_code == 404


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
    finally:
        srv.shutdown()


def test_browser_login(client):
    a = client.post(f"{API}/accounts", json={"name": "Browser"}).json()
    url = client.post(f"{API}/accounts/{a['id']}/login/start").json()["url"]
    assert url.startswith("https://claude.com/cai/oauth/authorize")
    r = client.post(f"{API}/accounts/{a['id']}/login/code", json={"code": "falsch#abc"})
    assert r.status_code == 400 and "fehlgeschlagen" in r.json()["detail"]
    client.post(f"{API}/accounts/{a['id']}/login/start")
    r = client.post(f"{API}/accounts/{a['id']}/login/code", json={"code": "good#abc"})
    assert r.status_code == 200 and r.json()["logged_in"] is True
    assert next(x for x in client.get(f"{API}/accounts").json() if x["id"] == a["id"])["logged_in"]
    assert client.post(f"{API}/accounts/{a['id']}/login/code", json={"code": "good#abc"}).status_code == 400  # no pending login
