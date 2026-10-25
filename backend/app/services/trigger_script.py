"""Trigger type "script": the user's own Python code checks MCP tools (no AI) and decides whether a routine starts.

The code runs in a separate Python process with a time limit and without the server's environment
(no database password, no tokens). It reaches the MCP connectors only through a one-time URL of this
server (/mcp/script/<token>), which is valid for exactly this check.
Only the user can write such code (agents cannot set triggers).
"""
import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

from ..config import get_settings

MARK = "@@IDA@@"
TIMEOUT_S = 60
MAX_CODE = 20000
MAX_STATE = 64 * 1024
RUNTIME = Path(__file__).with_name("script_runtime.py")
_tokens: set[str] = set()

EXAMPLE = '''# Läuft ohne KI. Startet die Routine nur, wenn fire(...) aufgerufen wird.
# tool(server, name, **args) ruft ein MCP-Tool auf und gibt den Text zurück.
mails = tool_json("google", "google_mails_suchen", query="is:unread newer_than:2d")
neue = new(mails if isinstance(mails, list) else [])   # nur noch nicht gesehene
if neue:
    fire(f"{len(neue)} neue Mail(s): " + ", ".join(str(m.get("subject", m)) for m in neue[:5]))
print("geprüft:", len(mails) if isinstance(mails, list) else mails[:80])
'''


def valid_token(token: str) -> bool:
    return token in _tokens


async def run(code: str, state: dict | None = None) -> dict:
    """-> {"fire", "info", "state", "log", "error"}"""
    token = secrets.token_hex(16)
    _tokens.add(token)
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    work = get_settings().sandbox_dir / "scripts"
    work.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"code": code, "state": state or {}, "url": f"{get_settings().internal_url}/mcp/script/{token}"})
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(RUNTIME), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=env, cwd=str(work))
        try:
            out, _ = await asyncio.wait_for(proc.communicate(payload.encode()), TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {"fire": False, "info": "", "state": state or {}, "log": "", "error": f"Abgebrochen: länger als {TIMEOUT_S} s"}
    finally:
        _tokens.discard(token)
    text = out.decode(errors="replace")
    log, _, tail = text.rpartition("\n" + MARK)
    try:
        res = json.loads(tail)
    except ValueError:
        return {"fire": False, "info": "", "state": state or {}, "log": text[-2000:], "error": "Skript ist abgestürzt"}
    new_state = res.get("state") or {}
    if len(json.dumps(new_state)) > MAX_STATE:
        res["error"] = res.get("error") or f"state ist zu groß (max. {MAX_STATE // 1024} KB)"
        new_state = state or {}
    return {"fire": bool(res.get("fire")) and not res.get("error"), "info": res.get("info") or "", "state": new_state,
            "log": log.strip()[-2000:], "error": res.get("error")}
