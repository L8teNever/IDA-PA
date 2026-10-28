"""Git in a project folder: status, diff, commit, push, branches – as subprocesses.

GitHub credentials reach git only through the process environment (an http.extraheader set via GIT_CONFIG_*),
never through the remote URL, the command line or a file – so they don't end up in .git/config or in logs.
"""
import asyncio
import base64
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from ...config import get_settings
from ..errors import Invalid

MAX_DIFF = 400_000  # characters of diff the browser gets at most
MAX_NEW_FILE = 100_000  # untracked files bigger than this are listed, not shown


def auth_env(account: dict | None) -> dict[str, str]:
    """Environment for git/gh (and the code agent's process): GitHub token + commit identity of the account."""
    env = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    if not account:
        return env
    if account.get("token"):
        basic = base64.b64encode(f"x-access-token:{account['token']}".encode()).decode()
        host = get_settings().github_url.rstrip("/")
        env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.{host}/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}", "GH_TOKEN": account["token"]})
    if account.get("login"):
        name, email = account.get("name") or account["login"], account.get("email") or f"{account['login']}@users.noreply.github.com"
        env.update({"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email})
    return env


def _clean(text: str) -> str:
    """Error output for the user – without anything that looks like a credential."""
    text = re.sub(r"(AUTHORIZATION: basic )\S+", r"\1***", text, flags=re.I)
    return re.sub(r"(https?://)[^/@\s]+@", r"\1", text).strip()


async def run(path: Path, *args: str, env: dict | None = None, ok: tuple[int, ...] = (0,), timeout: float = 600) -> str:
    base = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    proc = await asyncio.create_subprocess_exec(get_settings().git_bin, "-c", "core.quotepath=false", *args, cwd=path,
                                                env={**base, **(env or {})}, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise Invalid(f"git {args[0]} hat zu lange gedauert") from None
    if proc.returncode not in ok:
        raise Invalid(f"git {args[0]}: {_clean(err.decode(errors='replace') or out.decode(errors='replace'))[:600] or proc.returncode}")
    return out.decode(errors="replace")


async def has_commits(path: Path) -> bool:
    try:
        await run(path, "rev-parse", "--verify", "-q", "HEAD")
        return True
    except Invalid:
        return False


def _status_line(code: str) -> str:
    return {"M": "geändert", "A": "neu", "D": "gelöscht", "R": "umbenannt", "C": "kopiert", "?": "neu", "U": "Konflikt"}.get(code, code)


async def status(path: Path) -> dict:
    out = await run(path, "status", "--porcelain=v1", "-b", "-uall")
    lines = out.splitlines()
    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    branch, upstream, ahead, behind = head, None, 0, 0
    if head.startswith("No commits yet on "):
        branch = head.removeprefix("No commits yet on ")
    elif head.startswith("HEAD (no branch)"):
        branch = "HEAD"
    else:
        m = re.match(r"^(.+?)(?:\.\.\.(\S+))?(?: \[(.*)\])?$", head)
        if m:
            branch, upstream = m.group(1), m.group(2)
            info = m.group(3) or ""
            ahead = int((re.search(r"ahead (\d+)", info) or [0, 0])[1])
            behind = int((re.search(r"behind (\d+)", info) or [0, 0])[1])
    files = []
    for ln in lines[1:]:
        if len(ln) < 4:
            continue
        x, y, name = ln[0], ln[1], ln[3:]
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        code = "?" if x == "?" else (x if x != " " else y)
        files.append({"path": name.strip('"'), "code": code, "state": _status_line(code), "staged": x not in (" ", "?")})
    remote = None
    try:
        remote = (await run(path, "remote", "get-url", "origin")).strip() or None
    except Invalid:
        pass
    return {"branch": branch, "upstream": upstream, "ahead": ahead, "behind": behind, "files": files, "clean": not files,
            "has_commits": await has_commits(path), "remote": _public_url(remote)}


def _public_url(url: str | None) -> str | None:
    if not url:
        return None
    return re.sub(r"(https?://)[^/@\s]+@", r"\1", url)


def full_name(url: str | None) -> str | None:
    """owner/repo of a GitHub remote (https://github.com/owner/repo(.git) or git@github.com:owner/repo.git)."""
    if not url:
        return None
    host = urlparse(get_settings().github_url).netloc
    m = re.match(rf"^(?:https?://(?:[^@/]+@)?{re.escape(host)}/|git@{re.escape(host)}:)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _new_file_diff(path: Path, name: str) -> str:
    f = path / name
    try:
        size = f.stat().st_size
        if size > MAX_NEW_FILE:
            return f"diff --git a/{name} b/{name}\nnew file (zu groß für die Anzeige: {size // 1024} KB)\n"
        data = f.read_bytes()
    except OSError:
        return ""
    if b"\0" in data[:8000]:
        return f"diff --git a/{name} b/{name}\nnew file (binär)\n"
    lines = data.decode(errors="replace").splitlines()
    body = "".join(f"+{x}\n" for x in lines)
    return f"diff --git a/{name} b/{name}\nnew file\n--- /dev/null\n+++ b/{name}\n@@ -0,0 +1,{len(lines)} @@\n{body}"


async def diff(path: Path, file: str | None = None) -> dict:
    """Everything that changed since the last commit (incl. new files), or just one file."""
    st = await status(path)
    if file and file not in {f["path"] for f in st["files"]}:
        raise Invalid("Diese Datei hat keine Änderungen")
    parts = []
    tracked = [f["path"] for f in st["files"] if f["code"] != "?" and (not file or f["path"] == file)]
    if tracked:
        args = ["diff", "--no-color", "--no-ext-diff"] + (["HEAD"] if st["has_commits"] else ["--cached"]) + ["--", *tracked]
        parts.append(await run(path, *args))
    for f in st["files"]:
        if f["code"] == "?" and (not file or f["path"] == file):
            parts.append(_new_file_diff(path, f["path"]))
    text = "".join(parts)
    cut = len(text) > MAX_DIFF
    return {"diff": text[:MAX_DIFF], "truncated": cut, "files": st["files"]}


async def commit(path: Path, message: str, env: dict) -> dict:
    message = message.strip()
    if not message:
        raise Invalid("Commit-Nachricht fehlt")
    await run(path, "add", "-A", env=env)
    staged = await run(path, "diff", "--cached", "--name-only", env=env)
    if not staged.strip():
        raise Invalid("Nichts zu committen")
    if "GIT_AUTHOR_NAME" not in env:  # no GitHub account: commit as IDA instead of failing on a missing identity
        env = {**env, "GIT_AUTHOR_NAME": "IDA", "GIT_AUTHOR_EMAIL": "ida@localhost", "GIT_COMMITTER_NAME": "IDA", "GIT_COMMITTER_EMAIL": "ida@localhost"}
    await run(path, "commit", "-q", "-m", message, env=env)
    sha = (await run(path, "rev-parse", "--short", "HEAD")).strip()
    return {"sha": sha, "files": len(staged.splitlines())}


async def push(path: Path, env: dict) -> dict:
    st = await status(path)
    if not st["remote"]:
        raise Invalid("Das Projekt hat noch kein GitHub-Repo – erst veröffentlichen")
    if not st["has_commits"]:
        raise Invalid("Noch nichts committet")
    await run(path, "push", "-u", "origin", "HEAD", env=env)
    return {"branch": st["branch"]}


async def branches(path: Path) -> list[str]:
    out = await run(path, "branch", "--format=%(refname:short)")
    return [b for b in out.splitlines() if b.strip()]


async def switch(path: Path, name: str, create: bool) -> None:
    name = name.strip()
    try:
        await run(path, "check-ref-format", "--branch", name)
    except Invalid:
        raise Invalid(f"„{name}“ ist kein gültiger Branch-Name") from None
    await run(path, "switch", *(["-c", name] if create else [name]))
