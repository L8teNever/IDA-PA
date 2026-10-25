"""Google Antigravity as a second model provider – through Google's own CLI `agy` (like Claude Code for Claude).

Each Antigravity account has its own home below data/agy/<id>: `agy` keeps its login there (file based, there
is no keyring in the container) and IDA writes its settings before every run:
  - only the MCP servers of the run (workspace .agents/mcp_config.json; the global list is kept empty),
  - built-in tools (terminal, file writes, web) denied, terminal sandbox on, no access outside the workspace,
  - the process gets no server secrets in its environment.
Models are offered as "agy:<model>"; a run with such a model always uses an Antigravity account.
The stream-json format of `agy` is read leniently; the raw events of the last run are kept in
data/agy/<id>/last_run.jsonl to check the format.
"""
import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path

from ..config import get_settings
from ..models import Account
from ..models._types import new_id

log = logging.getLogger("ida.agy")
PREFIX = "agy:"
STATUS_MAX_AGE = 300
MAX_ARG = 120_000  # Linux limit for one argument is 128 KiB
DENY = ["command", "command(*)", "write_file", "write_file(*)", "edit_file(*)", "read_url", "read_url(*)", "browser(*)"]


def is_model(m: str | None) -> bool:
    return bool(m) and m.startswith(PREFIX) and len(m) > len(PREFIX)


def home(account_id: str) -> Path:
    d = get_settings().data_dir / "agy" / account_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def binary() -> str:
    exe = get_settings().agy_bin
    return shutil.which(exe) or exe


def command(*args: str) -> list[str]:
    exe = get_settings().agy_bin
    if " " in exe and not os.path.exists(exe):  # "python fake_agy.py" in tests
        return [*shlex.split(exe), *args]
    return [binary(), *args]


def env(account: Account) -> dict[str, str]:
    """Only what agy needs – no database password, no tokens of the server."""
    e = {"HOME": str(home(account.id)), "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
         "LANG": "C.UTF-8", "TZ": get_settings().timezone}
    if os.name == "nt":
        e["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    return e


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf8")


def lock_down(account_id: str) -> None:
    """agy settings for IDA: MCP tools run without asking, everything else is denied."""
    h = home(account_id)
    sp = h / ".gemini" / "antigravity-cli" / "settings.json"
    try:
        cur = json.loads(sp.read_text(encoding="utf8")) if sp.exists() else {}
    except ValueError:
        cur = {}
    cur.update({"toolPermission": "always-proceed", "permissions": {"allow": [], "ask": [], "deny": DENY},
                "enableTerminalSandbox": True, "allowNonWorkspaceAccess": False, "agentMode": "default",
                "disableSlashCommands": True})
    _write_json(sp, cur)
    _write_json(h / ".gemini" / "config" / "mcp_config.json", {"mcpServers": {}})


# ---------------- login status + models (from `agy models`, cached in status.json) ----------------
def _status_file(account_id: str) -> Path:
    return home(account_id) / "status.json"


def status(account: Account) -> dict:
    try:
        return json.loads(_status_file(account.id).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {"logged_in": False, "models": [], "checked": 0}


def logged_in(account: Account) -> bool:
    return bool(status(account).get("logged_in"))


def _parse_models(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^[\s*•\-–>]*([a-z][a-z0-9]*(?:[-.][a-z0-9]+)+)\b", line.strip(), re.I)
        if m and not m.group(1).lower().startswith(("http", "fetching", "error")):
            out.append(m.group(1))
    return list(dict.fromkeys(out))


async def refresh_status(account: Account, force: bool = False) -> dict:
    st = status(account)
    if not force and time.time() - st.get("checked", 0) < STATUS_MAX_AGE:
        return st
    try:
        proc = await asyncio.create_subprocess_exec(*command("models"), stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                    env=env(account), cwd=str(home(account.id)))
        out, _ = await asyncio.wait_for(proc.communicate(), 40)
        text = out.decode(errors="replace")
        ok = proc.returncode == 0 and not re.search(r"sign in|not logged|authentication", text, re.I)
        st = {"logged_in": ok, "models": _parse_models(text) if ok else st.get("models", []), "checked": time.time(),
              "raw": text[-1500:]}
    except (OSError, TimeoutError) as e:
        st = {**st, "checked": time.time(), "error": str(e)[:200]}
    _write_json(_status_file(account.id), st)
    return st


def models(accounts: list[Account]) -> list[str]:
    """agy:<model> of all logged-in Antigravity accounts (cached)."""
    out: list[str] = []
    for a in accounts:
        if a.provider == "antigravity" and logged_in(a):
            out += [PREFIX + m for m in status(a).get("models", [])]
    return list(dict.fromkeys(out))


def logout(account: Account) -> None:
    h = home(account.id)
    for p in (h / ".config" / "antigravity", h / ".gemini" / "antigravity-cli" / "credentials"):
        shutil.rmtree(p, ignore_errors=True)
    _write_json(_status_file(account.id), {"logged_in": False, "models": [], "checked": time.time()})


# ---------------- runs ----------------
def _prompt(spec) -> str:
    """agy has no system prompt flag: the instructions go in front of the message (only for new conversations)."""
    if spec.session_id:
        return spec.prompt
    text = f"<anweisungen>\n{spec.system}\n</anweisungen>\n\n{spec.prompt}" if spec.system else spec.prompt
    if len(text) > MAX_ARG:  # keep the newest part of a very long history
        text = text[:MAX_ARG // 3] + "\n…(gekürzt)…\n" + text[-(MAX_ARG * 2 // 3):]
    return text


def _text_of(x) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        for k in ("text", "delta", "content", "response"):
            if isinstance(x.get(k), (str, dict, list)):
                t = _text_of(x[k])
                if t:
                    return t
    if isinstance(x, list):
        return "".join(_text_of(i) for i in x)
    return ""


def _split_tool(name: str, server: str | None) -> tuple[str, str]:
    if server:
        return server, name
    m = re.match(r"^mcp_{1,2}(.+?)_{1,2}(.+)$", name or "")
    return (m.group(1), m.group(2)) if m else ("", name or "")


def normalize(ev: dict, seen_text: list[bool]) -> list[dict]:
    """One agy stream-json event -> IDA events (text / tool / tool_result / done). Lenient: the format is young."""
    kind = str(ev.get("event") or ev.get("type") or "")
    if kind == "result":
        r = ev.get("result") if isinstance(ev.get("result"), dict) else ev
        status = str(r.get("status") or "").upper()
        ok = status not in ("ERROR", "FAILED", "CANCELLED") and not r.get("error")
        text = str(r.get("response") or r.get("result") or "")
        out = []
        if ok and text and not seen_text[0]:  # nothing streamed: show the answer at once
            out.append({"type": "text", "text": text})
        out.append({"type": "done", "ok": ok, "result": text if ok else str(r.get("error") or text or "Fehler"),
                    "session_id": r.get("conversation_id") or None})
        return out
    data = ev.get("data") if isinstance(ev.get("data"), dict) else ev
    tool_obj = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    name = data.get("tool_name") or tool_obj.get("name") or (data.get("name") if ("tool" in kind or "input" in data) else None)
    tid = data.get("tool_use_id") or data.get("tool_call_id") or data.get("id") or new_id()
    if name and ("result" in kind or "output" in data or kind.endswith("_end") or kind.endswith("done")):
        return [{"type": "tool_result", "id": tid, "error": bool(data.get("error") or data.get("is_error")),
                 "content": _text_of(data.get("output") or data.get("result") or data.get("content"))[:4000]}]
    if name and ("tool" in kind or "call" in kind or "step" in kind or "input" in data):
        server, tool = _split_tool(str(name), data.get("server") or data.get("server_name"))
        return [{"type": "tool", "id": tid, "server": server, "name": tool, "input": data.get("input") or data.get("args") or {}}]
    if kind in ("thinking", "plan", "system", "init", "step", "error") or data.get("thinking"):
        return []
    text = _text_of(data.get("delta")) or (_text_of(data.get("text")) if kind in ("text", "assistant", "message", "delta", "text_delta", "content") else "")
    if text:
        seen_text[0] = True
        return [{"type": "text", "text": text}]
    return []


async def stream(spec) -> AsyncIterator[dict]:
    """Same events as claude.runner.stream, from `agy -p … --output-format stream-json`."""
    s = get_settings()
    acc = spec.account
    lock_down(acc.id)
    run_dir = s.sandbox_dir / "agy-runs" / new_id()
    servers = {k: {"serverUrl": v["url"], **({"headers": v["headers"]} if v.get("headers") else {}), "timeoutSeconds": 300}
               for k, v in spec.mcp_servers.items()}
    _write_json(run_dir / ".agents" / "mcp_config.json", {"mcpServers": servers})
    args = [*command(), "-p", _prompt(spec), "--output-format", "stream-json", "--sandbox", "--disable-slash-commands",
            "--print-timeout", f"{s.run_timeout_s}s"]
    model = spec.model[len(PREFIX):] if is_model(spec.model) else ""
    if model:
        args += ["--model", model]
    if spec.session_id and re.fullmatch(r"[\w-]+", spec.session_id):
        args += ["--conversation", spec.session_id]
    raw_log = home(acc.id) / "last_run.jsonl"
    proc = await asyncio.create_subprocess_exec(*args, cwd=str(run_dir), env=env(acc), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
    err: list[bytes] = []

    async def read_err():
        assert proc.stderr
        while chunk := await proc.stderr.read(4096):
            err.append(chunk)

    err_task = asyncio.create_task(read_err())
    seen_text, done = [False], False
    try:
        with raw_log.open("w", encoding="utf8") as raw:
            async with asyncio.timeout(s.run_timeout_s + 30):
                assert proc.stdout
                async for line in proc.stdout:
                    line = line.decode(errors="replace").strip()
                    if not line:
                        continue
                    raw.write(line[:4000] + "\n")
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    for out in normalize(ev, seen_text):
                        if out["type"] == "done":
                            if done:
                                continue
                            done = True
                        yield out
    except TimeoutError:
        proc.kill()
        if not done:
            done = True
            yield {"type": "done", "ok": False, "result": f"Abgebrochen: länger als {s.run_timeout_s} s", "session_id": None}
    finally:
        if proc.returncode is None:
            if not done:
                proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                proc.kill()
        await err_task
        shutil.rmtree(run_dir, ignore_errors=True)
    if not done:
        text = b"".join(err).decode(errors="replace").strip()
        yield {"type": "done", "ok": False, "result": text[-800:] or f"Beendet ohne Ergebnis (Code {proc.returncode})", "session_id": None}


def cached_models() -> list[str]:
    """agy:<model> from the status files of all logged-in accounts (no database needed)."""
    out: list[str] = []
    for f in sorted((get_settings().data_dir / "agy").glob("*/status.json")):
        try:
            st = json.loads(f.read_text(encoding="utf8"))
        except (OSError, ValueError):
            continue
        if st.get("logged_in"):
            out += [PREFIX + m for m in st.get("models", [])]
    return list(dict.fromkeys(out))
