"""Stand-in for Google's Antigravity CLI `agy` in tests.

  agy models            -> model list if logged in ($HOME/.config/antigravity/token exists), else the sign-in error
  agy -p PROMPT ...     -> stream-json events; "TOOL x {json}" in the prompt calls tool x of the IDA hub
                           (found in ./.agents/mcp_config.json like the real CLI); --conversation is echoed back
"""
import json
import os
import sys
import urllib.request

args = sys.argv[1:]
home = os.environ.get("HOME", "")
logged_in = os.path.exists(os.path.join(home, ".config", "antigravity", "token"))


def out(ev):
    print(json.dumps(ev), flush=True)


if args[:1] == ["models"]:
    if not logged_in:
        print("Error: Please sign in to view available models. Launch the CLI without arguments to sign in.")
        sys.exit(1)
    print("Fetching available models...\n  gemini-3-pro\n  gemini-3-flash\n  claude-sonnet-4-6")
    sys.exit(0)

prompt = args[args.index("-p") + 1] if "-p" in args else ""
model = args[args.index("--model") + 1] if "--model" in args else "default"
conv = args[args.index("--conversation") + 1] if "--conversation" in args else "conv-1"
if not logged_in:
    out({"event": "result", "result": {"conversation_id": "", "status": "ERROR", "response": "", "error": "authentication failed or timed out"}})
    sys.exit(1)
settings = json.load(open(os.path.join(home, ".gemini", "antigravity-cli", "settings.json"), encoding="utf8"))
assert "command(*)" in settings["permissions"]["deny"], "built-in tools must be denied"
mcp = json.load(open(os.path.join(".agents", "mcp_config.json"), encoding="utf8"))["mcpServers"]

if "TOOL " in prompt:
    line = prompt[prompt.index("TOOL "):].splitlines()[0]
    _, name, raw = line.split(" ", 2)
    out({"event": "tool_call", "tool_name": f"mcp_ida_{name}", "tool_use_id": "t1", "input": json.loads(raw)})
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": json.loads(raw)}}).encode()
    req = urllib.request.Request(mcp["ida"]["serverUrl"], data=body, headers={"content-type": "application/json"})
    res = json.loads(urllib.request.urlopen(req).read())["result"]["content"][0]["text"]
    out({"event": "tool_result", "tool_name": f"mcp_ida_{name}", "tool_use_id": "t1", "output": res})
    text = f"[agy|{model}] {res}"
else:
    user = prompt.split("</anweisungen>")[-1].strip()
    text = f"[agy|{model}] {user}" + (" (mit Anweisungen)" if "<anweisungen>" in prompt else "")
for i in range(0, len(text), 30):
    out({"event": "text", "delta": text[i:i + 30]})
out({"event": "result", "result": {"conversation_id": conv, "status": "SUCCESS", "response": text, "error": ""}})
