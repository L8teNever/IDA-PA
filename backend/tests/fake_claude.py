"""Stand-in for the Claude Code CLI in tests: speaks the same stream-json protocol.

Behaviour (decided by the prompt text):
  LIMIT     -> account a1 answers with a usage-limit error (fallback tests)
  TOOL x y  -> calls tool x of the internal MCP with JSON args y, answers with the tool result
  otherwise -> echoes the prompt; the system prompt is appended after "SYSTEM:" when the prompt contains SHOWSYS
"""
import json
import os
import sys
import urllib.request

args = sys.argv[1:]
if args[:2] == ["auth", "login"]:  # browser login: print URL, wait for the code, store credentials
    print("Opening browser to sign in…", flush=True)
    print("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=abc", flush=True)
    print("Paste code here if prompted > ", end="", flush=True)
    code = sys.stdin.readline().strip()
    if code != "good#abc":
        print("OAuth error: invalid code", flush=True)
        sys.exit(1)
    path = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], ".credentials.json")
    with open(path, "w", encoding="utf8") as f:
        json.dump({"claudeAiOauth": {"accessToken": "sk-ant-oat-test", "refreshToken": "r", "expiresAt": 0}}, f)
    print("Login successful.", flush=True)
    sys.exit(0)
if args[:1] == ["auth"]:
    print("{}")
    sys.exit(0)


def arg(name):
    return args[args.index(name) + 1] if name in args else None


prompt = sys.stdin.read()
system = open(arg("--system-prompt-file"), encoding="utf8").read()
mcp = json.load(open(arg("--mcp-config"), encoding="utf8"))["mcpServers"]
account = os.path.basename(os.environ.get("CLAUDE_CONFIG_DIR", ""))
session = arg("--resume") or "sess-" + account


def out(ev):
    print(json.dumps(ev), flush=True)


def hub(name, a):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": a}}).encode()
    req = urllib.request.Request(mcp["ida"]["url"], data=body, headers={"content-type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())["result"]["content"][0]["text"]


if "LIMIT" in prompt and account == "a1":
    out({"type": "result", "is_error": True, "result": "You've hit your session limit · resets 3pm", "session_id": session})
    sys.exit(1)

if "TOOL " in prompt:
    line = prompt[prompt.index("TOOL "):].splitlines()[0]
    _, name, raw = line.split(" ", 2)
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": f"mcp__ida__{name}", "input": json.loads(raw)}]}})
    res = hub(name, json.loads(raw))
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": res}]}]}})
    text = f"[{account}] {res}"
else:
    text = f"[{account}|{arg('--model')}] {prompt}" + (f"\nSYSTEM:{system}" if "SHOWSYS" in prompt else "")

for i in range(0, len(text), 40):
    out({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": text[i:i + 40]}}})
out({"type": "result", "is_error": False, "result": text, "session_id": session})
