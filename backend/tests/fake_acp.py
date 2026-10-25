"""Stand-in for Google's Antigravity ACP agent (agy_acp_server) in tests – JSON-RPC over stdin/stdout like the real one.

  initialize                -> capabilities + auth methods
  authenticate              -> done at once if $GEMINI_HOME/antigravity-acp/acp_token.json exists; otherwise it "opens the
                               browser" ($BROWSER) with a Google link whose redirect_uri is a local HTTP listener – a GET
                               with ?code=…&state=… there writes the token file and finishes authenticate
  session/new               -> sessionId + config option "model" (the account's models)
  session/set_config_option -> remembers the model
  session/prompt            -> streams session/update (message chunks, tool calls) and ends with stopReason end_turn;
                               "TOOL x {json}" calls tool x of the session's MCP server "ida"
"""
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.request

home = os.environ["GEMINI_HOME"]
token = os.path.join(home, "antigravity-acp", "acp_token.json")
out_lock = threading.Lock()
sessions = {}
MODELS = [{"value": "gemini-3.1-pro-high", "name": "Gemini 3.1 Pro (High)"}, {"value": "gemini-3-flash", "name": "Gemini 3 Flash"},
          {"value": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)"}]


def send(msg):
    with out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def reply(i, result):
    send({"jsonrpc": "2.0", "id": i, "result": result})


def authenticate(i):
    if os.path.exists(token):
        return reply(i, {})
    state = secrets.token_hex(8)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            ok = q.get("state") == [state] and q.get("code")
            self.send_response(200 if ok else 400)
            self.end_headers()
            self.wfile.write(b"ok")
            if ok:
                os.makedirs(os.path.dirname(token), exist_ok=True)
                json.dump({"client_id": "c", "client_secret": "s", "refresh_token": "r"}, open(token, "w"))
                reply(i, {})
                threading.Thread(target=srv.shutdown, daemon=True).start()

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = ("https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id=x&redirect_uri="
           f"http%3A%2F%2F127.0.0.1%3A{srv.server_port}%2F&state={state}")
    browser = os.environ.get("BROWSER", "").replace("%s", "").strip()
    if browser and not browser.endswith(".sh") or os.name == "nt":
        sys.stderr.write("Open the following link to authenticate the ACP server: " + url + "\n")
        sys.stderr.flush()
    else:
        subprocess.run(["sh", browser, url])


def prompt(i, p):
    sid = p["sessionId"]
    s = sessions[sid]
    text = "".join(x.get("text", "") for x in p["prompt"])
    model = s.get("model", "default")

    def upd(u):
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})
    if "TOOL " in text:
        line = text[text.index("TOOL "):].splitlines()[0]
        _, name, raw = line.split(" ", 2)
        upd({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": f"ida/{name}", "kind": "other", "status": "in_progress", "rawInput": json.loads(raw)})
        srv = next(x for x in s["mcp"] if x["name"] == "ida")
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": json.loads(raw)}}).encode()
        res = json.loads(urllib.request.urlopen(urllib.request.Request(srv["url"], data=body, headers={"content-type": "application/json"})).read())
        res = res["result"]["content"][0]["text"]
        upd({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed", "content": [{"type": "content", "content": {"type": "text", "text": res}}]})
        answer = f"[acp|{model}] {res}"
    else:
        user = text.split("</anweisungen>")[-1].strip()
        answer = f"[acp|{model}] {user}" + (" (mit Anweisungen)" if "<anweisungen>" in text else "")
    upd({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "hmm"}})
    for k in range(0, len(answer), 25):
        upd({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": answer[k:k + 25]}})
    reply(i, {"stopReason": "end_turn"})


for raw in sys.stdin:
    msg = json.loads(raw)
    m, i, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if m == "initialize":
        reply(i, {"protocolVersion": 1, "authMethods": [{"id": "oauth-personal", "name": "Log in with Google"}],
                  "agentCapabilities": {"mcpCapabilities": {"http": True}}})
    elif m == "authenticate":
        threading.Thread(target=authenticate, args=(i,), daemon=True).start()
    elif m == "session/new":
        if not os.path.exists(token):
            send({"jsonrpc": "2.0", "id": i, "error": {"code": -32000, "message": "Authentication required"}})
            continue
        sid = "s" + secrets.token_hex(4)
        sessions[sid] = {"mcp": p.get("mcpServers") or []}
        reply(i, {"sessionId": sid, "configOptions": [{"id": "model", "type": "select", "currentValue": MODELS[0]["value"],
                                                        "options": [{"group": "Modelle", "options": MODELS}]}]})
    elif m == "session/set_config_option":
        sessions[p["sessionId"]]["model"] = p["value"]
        reply(i, {"configOptions": []})
    elif m == "session/prompt":
        threading.Thread(target=prompt, args=(i, p), daemon=True).start()
    elif m == "logout":
        if os.path.exists(token):
            os.remove(token)
        reply(i, {})
    elif i is not None:
        send({"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "unknown"}})
