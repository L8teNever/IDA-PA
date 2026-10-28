"""Runs the Claude Code CLI as a fresh, locked-down session and reads its stream-json output.

Every run gets: no built-in tools, no user settings/memory/skills of the account, an empty working dir,
only the MCP servers passed in, an allow/deny list for their tools and its own system prompt.

Code threads (spec.code) are the exception: they work in their project folder with Claude's own tools
(files, shell, git) under a permission mode; in "ask" mode every edit/command goes to the user first
(permission tool mcp__ida__code_approve) and the run waits for the answer – so it has no time limit.
"""
import asyncio
import base64
import json
import logging
import os
import re
import shutil
import sys
from contextlib import aclosing
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from ...config import get_settings
from ...models import Account
from ...models._types import new_id
from .. import activity

log = logging.getLogger("ida.claude")

# The CLI prints e.g. "You've hit your session limit · resets 3pm" when an account is at its limit.
LIMIT_RE = re.compile(r"hit your .{0,30}limit|usage limit|session limit|weekly limit|limit reached|rate limit|resets? (at )?\d", re.I)

BASE_SYSTEM = (
    "Du bist ein hilfreicher Assistent ohne Vorwissen ueber den Rechner, auf dem du laeufst. "
    "Nutze ausschliesslich, was in dieser Unterhaltung steht oder was dir die bereitgestellten Tools liefern. "
    "Informationen aus deiner Umgebung (Konto, E-Mail-Adresse, Betriebssystem, Verzeichnisse, Git) darfst du weder verwenden noch nennen."
)


def account_dir(account_id: str) -> Path:
    d = get_settings().claude_dir / account_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def account_env(account: Account, code: bool = False) -> dict[str, str]:
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(account_dir(account.id)), "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    if not code:  # code threads work in a real repo: there git instructions help
        env["CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS"] = "1"
    if account.oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = account.oauth_token
    return env


def norm(name: str) -> str:
    """Tool names as the CLI exposes them: mcp__<server>__<tool> with [^\\w-] replaced."""
    return re.sub(r"[^\w-]", "_", name)


@dataclass
class RunSpec:
    model: str
    account: Account
    prompt: str
    system: str = ""
    mcp_servers: dict = field(default_factory=dict)  # name -> {"type":"http","url":…,"headers":{…}}
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    session_id: str | None = None  # resume this Claude session
    persist: bool = False  # keep the session for later resumes (chats)
    agent_id: str | None = None  # whose run this is (shown as the mascot's mood)
    kind: str = "run"  # chat | run
    effort: str | None = None  # low … max; None = the model's default
    # pictures / PDFs for the model: [{"kind": "image"|"pdf", "mime", "path", "name"}] (text files are in the prompt)
    attachments: list[dict] = field(default_factory=list)
    code: "CodeRun | None" = None  # code thread: real tools in the project folder


# --permission-mode per code thread: ask = every edit/command needs the user's OK, edits = file edits go through,
# full = everything goes through
PERMISSION_MODES = {"ask": "default", "edits": "acceptEdits", "full": "bypassPermissions"}
APPROVE_TOOL = "mcp__ida__code_approve"


@dataclass
class CodeRun:
    cwd: Path
    mode: str = "ask"  # ask | edits | full
    env: dict[str, str] = field(default_factory=dict)  # git identity / GitHub token for git and gh


@dataclass
class RunResult:
    ok: bool
    text: str
    tools: list[str]
    session_id: str | None = None
    account: str = ""

    @property
    def limit_hit(self) -> bool:
        return not self.ok and bool(LIMIT_RE.search(self.text))


def _kill(proc: asyncio.subprocess.Process) -> None:
    """Stops the CLI. On Windows it runs behind cmd /c – end the whole process tree, not just cmd."""
    if proc.returncode is not None:
        return
    if sys.platform == "win32":
        import subprocess
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def _command(args: list[str]) -> list[str]:
    exe = get_settings().claude_bin
    found = shutil.which(exe) or exe
    if sys.platform == "win32" and found.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", found, *args]
    return [found, *args]


async def stream(spec: RunSpec) -> AsyncIterator[dict]:
    """Yields normalised events:
    {"type":"text","text"}, {"type":"tool","id","server","name","input"},
    {"type":"tool_result","id","error","content"}, {"type":"done","ok","result","session_id"}.
    Antigravity accounts run through the agy CLI (services/antigravity.py) with the same events.
    """
    from .. import antigravity
    rid = activity.start(spec.agent_id, spec.kind) if spec.agent_id else None
    ok = False
    source = antigravity.stream(spec) if getattr(spec.account, "provider", "claude") == "antigravity" else _stream(spec)
    try:
        async with aclosing(source) as events:  # closing it stops the CLI if our consumer goes away
            async for ev in events:
                if rid:
                    if ev["type"] == "text":
                        activity.step(rid, "writing")
                    elif ev["type"] == "tool":
                        activity.step(rid, "tool", ev["name"], ev["server"])
                    elif ev["type"] == "done":
                        ok = ev["ok"]
                yield ev
    finally:
        if rid:
            activity.end(rid, ok)


async def _stream(spec: RunSpec) -> AsyncIterator[dict]:
    s = get_settings()
    run_dir = s.sandbox_dir / "runs" / new_id()
    run_dir.mkdir(parents=True, exist_ok=True)
    code = spec.code
    work = code.cwd if code else s.sandbox_dir / "work"  # fixed cwd: Claude stores sessions per working dir, resume needs the same one
    work.mkdir(parents=True, exist_ok=True)
    system = spec.system if code else BASE_SYSTEM + ("\n" + spec.system if spec.system else "")
    (run_dir / "system.txt").write_text(system, encoding="utf8")
    (run_dir / "mcp.json").write_text(json.dumps({"mcpServers": spec.mcp_servers}), encoding="utf8")
    (run_dir / "settings.json").write_text(json.dumps({"permissions": {"allow": spec.allow, "deny": spec.deny}}), encoding="utf8")
    args = ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", spec.model, *([] if code else ["--tools", ""]), "--setting-sources", "project", "--disable-slash-commands",
            "--system-prompt-file", str(run_dir / "system.txt"),
            "--strict-mcp-config", "--mcp-config", str(run_dir / "mcp.json"),
            "--settings", str(run_dir / "settings.json")]
    if code:
        args += ["--permission-mode", PERMISSION_MODES.get(code.mode, "default")]
        if code.mode != "full":  # whatever the mode does not allow by itself is asked through the user
            args += ["--permission-prompt-tool", APPROVE_TOOL]
    if spec.effort:
        args += ["--effort", spec.effort]
    if spec.attachments:  # the message as JSON with picture/document blocks instead of plain text
        args += ["--input-format", "stream-json"]
    if not spec.persist:
        args.append("--no-session-persistence")
    if spec.session_id and re.fullmatch(r"[\w-]+", spec.session_id):
        args += ["--resume", spec.session_id]

    env = account_env(spec.account, code=bool(code))
    if code:
        # a tool call may wait for the user's approval as long as it takes
        env.update({**code.env, "MCP_TOOL_TIMEOUT": str(2**31 - 1)})
    proc = await asyncio.create_subprocess_exec(
        *_command(args), cwd=work, env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )
    stderr_chunks: list[bytes] = []

    async def read_err():
        assert proc.stderr
        while chunk := await proc.stderr.read(4096):
            stderr_chunks.append(chunk)

    err_task = asyncio.create_task(read_err())
    assert proc.stdin and proc.stdout
    proc.stdin.write(_stdin(spec))
    await proc.stdin.drain()
    proc.stdin.close()

    done = False
    try:
        async with asyncio.timeout(None if code else s.run_timeout_s):  # code: may wait for approvals
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type")
                if t == "stream_event":
                    d = (ev.get("event") or {}).get("delta") or {}
                    if d.get("type") == "text_delta":
                        yield {"type": "text", "text": d.get("text", "")}
                elif t in ("assistant", "user"):
                    for b in (ev.get("message") or {}).get("content") or []:
                        if b.get("type") == "tool_use":
                            m = re.match(r"^mcp__(.+?)__(.+)$", b.get("name", ""))
                            yield {"type": "tool", "id": b.get("id"), "server": m.group(1) if m else "",
                                   "name": m.group(2) if m else b.get("name"), "input": b.get("input")}
                        elif b.get("type") == "tool_result":
                            c = b.get("content")
                            text = "\n".join(x.get("text", "") for x in c) if isinstance(c, list) else str(c or "")
                            yield {"type": "tool_result", "id": b.get("tool_use_id"), "error": bool(b.get("is_error")), "content": text[:4000]}
                elif t == "result":
                    done = True
                    yield {"type": "done", "ok": not ev.get("is_error"), "result": str(ev.get("result") or ""), "session_id": ev.get("session_id")}
    except TimeoutError:
        _kill(proc)
        yield {"type": "done", "ok": False, "result": f"Abgebrochen: länger als {s.run_timeout_s} s", "session_id": None}
        done = True
    finally:
        if proc.returncode is None:
            if not done:  # consumer went away (e.g. browser closed) – stop the run
                _kill(proc)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                _kill(proc)
        await err_task
        shutil.rmtree(run_dir, ignore_errors=True)
    if not done:
        err = b"".join(stderr_chunks).decode(errors="replace").strip()
        yield {"type": "done", "ok": False, "result": err[:800] or f"Beendet ohne Ergebnis (Code {proc.returncode})", "session_id": None}


def blocks(spec: RunSpec) -> list[dict]:
    """The message for Claude: the text, then pictures (image) and PDFs (document) as base64."""
    out: list[dict] = [{"type": "text", "text": spec.prompt}]
    for a in spec.attachments:
        data = base64.b64encode(Path(a["path"]).read_bytes()).decode()
        typ = "image" if a["kind"] == "image" else "document"
        out.append({"type": typ, "source": {"type": "base64", "media_type": a["mime"], "data": data},
                    **({"title": a["name"]} if typ == "document" else {})})
    return out


def _stdin(spec: RunSpec) -> bytes:
    if not spec.attachments:
        return spec.prompt.encode()
    return (json.dumps({"type": "user", "message": {"role": "user", "content": blocks(spec)}}) + "\n").encode()


async def run(spec: RunSpec) -> RunResult:
    """Runs to completion; collects the final text and the names of the tools that were used."""
    tools: list[str] = []
    result = RunResult(ok=False, text="", tools=tools, account=spec.account.name)
    async for ev in stream(spec):
        if ev["type"] == "tool":
            tools.append(f"{ev['server']} › {ev['name']}" if ev["server"] else ev["name"])
        elif ev["type"] == "done":
            result.ok, result.text, result.session_id = ev["ok"], ev["result"], ev["session_id"]
    return result
