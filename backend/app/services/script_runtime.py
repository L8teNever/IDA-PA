"""Runs the user's trigger script in its own Python process (started by services/trigger_script.py).

Reads {"code", "state", "url"} from stdin, runs the code with a few helpers and prints the result as the
last line ("@@IDA@@" + JSON). Everything the script prints is shown to the user as its log.

Helpers inside the script:
  tool(server, name, **args)       -> text result of an MCP tool (no AI involved)
  tool_json(server, name, **args)  -> the result parsed as JSON (falls back to the text)
  new(items, key=None)             -> only the items not seen in earlier checks (the first check only remembers)
  state                            -> dict that is kept between checks
  fire(info="")                    -> start the routine; info is handed to the agent
"""
import json
import string
import sys
import urllib.request

MARK = "@@IDA@@"
WS = string.whitespace + ","  # separators between JSON objects
_in = json.loads(sys.stdin.read())
_fired: list[str] = []


def tool(server: str, name: str, **args) -> str:
    body = json.dumps({"connector": server, "tool": name, "args": args}).encode()
    req = urllib.request.Request(_in["url"], data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        res = json.loads(r.read())
    if res.get("error"):
        raise RuntimeError(f"{server}.{name}: {res['error']}")
    return res["text"]


def tool_json(server: str, name: str, **args):
    """JSON result; several JSON objects one after another (some tools answer like that) become a list."""
    text = tool(server, name, **args)
    try:
        return json.loads(text)
    except ValueError:
        pass
    dec, pos, items = json.JSONDecoder(), 0, []
    try:
        while pos < len(text):
            while pos < len(text) and text[pos] in WS:
                pos += 1
            if pos >= len(text):
                break
            obj, pos = dec.raw_decode(text, pos)
            items.append(obj)
    except ValueError:
        return text
    return items if items else text


def new(items, key=None):
    key = key or (lambda x: x.get("id", json.dumps(x, sort_keys=True)) if isinstance(x, dict) else x)
    seen = state.get("_seen")
    keys = [str(key(i)) for i in items]
    state["_seen"] = (list(seen or []) + [k for k in keys if k not in (seen or [])])[-1000:]
    if seen is None:  # first check: only remember what is there
        return []
    return [i for i, k in zip(items, keys) if k not in seen]


def fire(info: str = "") -> None:
    _fired.append(str(info))


state = _in.get("state") or {}
_result = {"fire": False, "info": "", "error": None}
try:
    exec(compile(_in["code"], "<skript>", "exec"), {"__name__": "__skript__", "tool": tool, "tool_json": tool_json,
                                                    "new": new, "fire": fire, "state": state, "json": json})
except SystemExit:
    pass
except BaseException as e:  # noqa: BLE001 – report every error of the user's code
    import traceback
    tb = traceback.extract_tb(e.__traceback__)
    line = next((f.lineno for f in reversed(tb) if f.filename == "<skript>"), None)
    _result["error"] = f"{e.__class__.__name__}: {e}" + (f" (Zeile {line})" if line else "")
_result.update(fire=bool(_fired), info="\n".join(x for x in _fired if x)[:4000], state=state)
try:
    out = json.dumps(_result, default=str)
except (TypeError, ValueError):
    _result["state"], _result["error"] = {}, _result["error"] or "state lässt sich nicht speichern (nur JSON-Werte)"
    out = json.dumps(_result, default=str)
print("\n" + MARK + out, flush=True)
