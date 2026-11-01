"""Stand-in for Google's Antigravity ACP agent (agy_acp_server) in tests – JSON-RPC over stdin/stdout like the real one.

  initialize                -> capabilities + auth methods
  authenticate              -> done at once if $GEMINI_HOME/antigravity-acp/acp_token.json exists; otherwise it "opens the
                               browser" ($BROWSER) with a Google link whose redirect_uri is a local HTTP listener – a GET
                               with ?code=…&state=… there writes the token file and finishes authenticate
  session/new               -> sessionId + config option "model" (the account's models); remembers cwd
  session/set_config_option -> remembers model or mode
  session/set_mode          -> remembers mode
  session/prompt            -> streams session/update (message chunks, tool calls) and ends with stopReason end_turn;
                               "TOOL x {json}" calls tool x of the session's MCP server "ida"
                               "PWD" echoes cwd/mode (code threads); "WRITE f text" asks session/request_permission first
"""
import http.server
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import urllib.request

home = os.environ["GEMINI_HOME"]
token = os.path.join(home, "antigravity-acp", "acp_token.json")
out_lock = threading.Lock()
sessions = {}
replies = {}  # request id -> Queue for client responses (permissions)
next_req = 1000
# select = base list (Gemini + Claude + GPT); availableModels = many Gemini thinking variants
# (regression: preferring the longer variants list used to drop Claude/GPT from the picker)
MODELS = [{"value": "gemini-3.1-pro-high", "name": "Gemini 3.1 Pro (High)"}, {"value": "gemini-3-flash", "name": "Gemini 3 Flash"},
          {"value": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)"},
          {"value": "gpt-oss-120b-medium", "name": "GPT-OSS 120B (Medium)"}]
AVAILABLE = [
    {"modelId": "gemini-3-flash-low", "name": "Gemini 3 Flash (low)"},
    {"modelId": "gemini-3-flash-medium", "name": "Gemini 3 Flash (medium)"},
    {"modelId": "gemini-3-flash-high", "name": "Gemini 3 Flash (high)"},
    {"modelId": "gemini-3.1-pro-low", "name": "Gemini 3.1 Pro (low)"},
    {"modelId": "gemini-3.1-pro-medium", "name": "Gemini 3.1 Pro (medium)"},
    {"modelId": "gemini-3.1-pro-high", "name": "Gemini 3.1 Pro (high)"},
]


def send(msg):
    with out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def reply(i, result):
    send({"jsonrpc": "2.0", "id": i, "result": result})


def ask_client(method, params, timeout=120):
    """Send a JSON-RPC request to the client (IDA) and wait for its response (e.g. permissions)."""
    global next_req
    next_req += 1
    rid = next_req
    q = queue.Queue()
    replies[rid] = q
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    try:
        msg = q.get(timeout=timeout)
    finally:
        replies.pop(rid, None)
    if "error" in msg:
        raise RuntimeError(msg["error"])
    return msg.get("result")


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
    mode = s.get("mode", "default")
    cwd = s.get("cwd") or os.getcwd()

    def upd(u):
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})

    # follow-ups send the whole history – use the newest instruction (last match / after header)
    focus = text.split("Neue Nachricht des Nutzers:\n")[-1] if "Neue Nachricht des Nutzers:" in text else text
    if "WRITE " in focus:
        _, name, content = focus[focus.rindex("WRITE "):].splitlines()[0].split(" ", 2)
        path = os.path.join(cwd, name)
        inp = {"file_path": path, "content": content}
        options = [{"optionId": "allow", "kind": "allow_once", "name": "Allow"},
                   {"optionId": "deny", "kind": "reject_once", "name": "Deny"}]
        res = ask_client("session/request_permission", {
            "sessionId": sid,
            "toolCall": {"toolCallId": "w1", "title": "Write", "kind": "edit", "rawInput": inp},
            "options": options,
        })
        outcome = (res or {}).get("outcome") or {}
        allowed = outcome.get("outcome") == "selected" and outcome.get("optionId") == "allow"
        if allowed:
            open(path, "w", encoding="utf8").write(content)
            answer = f"[acp|{model}] {name} geschrieben"
        else:
            answer = f"[acp|{model}] abgelehnt"
    elif "PWD" in focus:
        answer = f"[acp|{model}] cwd={cwd} mode={mode}"
    elif "TOOL " in focus:
        line = focus[focus.index("TOOL "):].splitlines()[0]
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
    # client response to our request (e.g. permission decision)
    if i is not None and "method" not in msg and ("result" in msg or "error" in msg) and i in replies:
        replies[i].put(msg)
        continue
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
        sessions[sid] = {"mcp": p.get("mcpServers") or [], "cwd": p.get("cwd"), "mode": "default"}
        reply(i, {"sessionId": sid, "models": {"availableModels": AVAILABLE},
                  "configOptions": [{"id": "model", "type": "select", "currentValue": MODELS[0]["value"],
                                    "options": [{"group": "Modelle", "options": MODELS}]}]})
    elif m == "session/set_config_option":
        s = sessions[p["sessionId"]]
        if p.get("configId") == "mode":
            s["mode"] = p["value"]
        else:
            s["model"] = p["value"]
        reply(i, {"configOptions": []})
    elif m == "session/set_mode":
        sessions[p["sessionId"]]["mode"] = p.get("modeId") or p.get("value")
        reply(i, {})
    elif m == "session/prompt":
        threading.Thread(target=prompt, args=(i, p), daemon=True).start()
    elif m == "logout":
        if os.path.exists(token):
            os.remove(token)
        reply(i, {})
    elif i is not None:
        send({"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "unknown"}})
