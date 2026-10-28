"""Code projects: a git repository per project in data/projects/<id> – cloned (GitHub/URL) or started empty."""
import asyncio
from datetime import datetime, timezone
import logging
import mimetypes
import os
from pathlib import Path
import shutil
import stat
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db import session_scope
from ...models import Chat, CodeProject
from .. import chat as chat_service
from .. import files as file_svc
from ..errors import Invalid, NotFound
from . import approvals, git, github

log = logging.getLogger("ida.code")
_clones: dict[str, asyncio.subprocess.Process] = {}  # project id -> running git clone
_tasks: set[asyncio.Task] = set()


def root() -> Path:
    r = get_settings().projects_dir
    r.mkdir(parents=True, exist_ok=True)
    return r.resolve()


def path_of(p: CodeProject) -> Path:
    """The project's folder – always directly below data/projects (ids are generated, but check anyway)."""
    d = (root() / p.id).resolve()
    if d.parent != root():
        raise Invalid("Ungültiger Projektpfad")
    return d


async def get(db: AsyncSession, pid: str) -> CodeProject:
    p = await db.get(CodeProject, pid)
    if not p:
        raise NotFound("Projekt nicht gefunden")
    return p


async def ready(db: AsyncSession, pid: str) -> tuple[CodeProject, Path]:
    p = await get(db, pid)
    if p.status == "cloning":
        raise Invalid("Das Projekt wird noch geklont – einen Moment")
    d = path_of(p)
    if p.status == "error" or not d.is_dir():
        raise Invalid(f"Das Projekt ist nicht bereit: {p.status_text or 'Ordner fehlt'}")
    return p, d


async def listing(db: AsyncSession) -> list[tuple[CodeProject, int, int]]:
    """Projects (newest activity first) with the number of threads and how many of them are running."""
    rows = (await db.scalars(select(CodeProject).order_by(CodeProject.updated_at.desc()))).all()
    out = []
    for p in rows:
        ids = (await db.scalars(select(Chat.id).where(Chat.project_id == p.id))).all()
        out.append((p, len(ids), sum(chat_service.running(i) for i in ids)))
    return out


def _check_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("https://", "file://")):
        raise Invalid("Die Repo-Adresse muss mit https:// beginnen")
    if "@" in url.split("://", 1)[1].split("/", 1)[0]:
        raise Invalid("Bitte die Adresse ohne Zugangsdaten angeben – die Anmeldung läuft über Einstellungen → GitHub")
    return url


async def create(db: AsyncSession, name: str, repo_url: str | None = None) -> CodeProject:
    name = (name or "").strip()
    if repo_url:
        repo_url = _check_url(repo_url)
        if not name:
            name = repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    if not name:
        raise Invalid("Name fehlt")
    p = CodeProject(name=name[:80], repo_url=repo_url or None, status="cloning" if repo_url else "ready")
    db.add(p)
    await db.flush()
    d = path_of(p)
    if repo_url:
        env = git.auth_env(await github.account(db))
        task = asyncio.create_task(_clone(p.id, repo_url, d, env))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    else:
        d.mkdir(parents=True, exist_ok=True)
        await git.run(d, "init", "-q", "-b", "main")
        p.default_branch = "main"
    return p


async def _clone(pid: str, url: str, dest: Path, env: dict) -> None:
    """git clone in the background; the project shows the progress and becomes ready (or error) at the end."""
    await asyncio.sleep(0.05)  # the request that created the project commits first
    ok, text = False, ""
    try:
        proc = await asyncio.create_subprocess_exec(get_settings().git_bin, "clone", "--progress", url, str(dest),
                                                    env={**os.environ, **env}, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _clones[pid] = proc
        assert proc.stderr
        buf, last = b"", 0.0
        tail: list[str] = []
        while chunk := await proc.stderr.read(1024):
            buf += chunk
            *done, buf = buf.replace(b"\r", b"\n").split(b"\n")
            for ln in done:
                s = git._clean(ln.decode(errors="replace"))
                if s:
                    tail = (tail + [s])[-5:]
            now = asyncio.get_running_loop().time()
            if tail and now - last > 1:
                last = now
                async with session_scope() as db:
                    if p := await db.get(CodeProject, pid):
                        p.status_text = tail[-1][:300]
        await proc.wait()
        ok = proc.returncode == 0
        text = "" if ok else "\n".join(tail)[-600:] or f"git clone fehlgeschlagen (Code {proc.returncode})"
    except Exception as e:  # noqa: BLE001 – shown on the project
        log.exception("clone %s failed", pid)
        text = str(e)[:600] or e.__class__.__name__
    finally:
        _clones.pop(pid, None)
    branch = None
    if ok:
        try:
            branch = (await git.run(dest, "rev-parse", "--abbrev-ref", "HEAD")).strip()
        except Invalid:
            branch = None
    async with session_scope() as db:
        p = await db.get(CodeProject, pid)
        if not p:  # deleted meanwhile
            _rmtree(dest)
            return
        p.status, p.status_text = ("ready", "") if ok else ("error", text)
        if branch and branch != "HEAD":
            p.default_branch = branch


def _rmtree(d: Path) -> None:
    if not d.exists():
        return

    def writable(func, path, *_):  # git objects are read-only (Windows refuses to delete them otherwise)
        os.chmod(path, stat.S_IWRITE)
        func(path)
    shutil.rmtree(d, **({"onexc": writable} if sys.version_info >= (3, 12) else {"onerror": writable}))


async def delete(db: AsyncSession, pid: str) -> None:
    """The project, its threads (answers are stopped first, attachments removed) and its folder."""
    p = await get(db, pid)
    proc = _clones.pop(pid, None)
    if proc and proc.returncode is None:
        proc.kill()
    for cid in (await db.scalars(select(Chat.id).where(Chat.project_id == pid))).all():
        chat_service.stop(cid)
        approvals.cancel_chat(cid)
        await file_svc.delete_for_chat(db, cid)
        if c := await db.get(Chat, cid):
            await db.delete(c)
    d = path_of(p)
    await db.delete(p)
    await db.flush()
    try:
        await asyncio.to_thread(_rmtree, d)
    except OSError as e:
        log.warning("could not remove %s: %s", d, e)


async def publish(db: AsyncSession, pid: str, name: str, private: bool) -> CodeProject:
    """Creates a GitHub repo for a local project, sets it as origin and pushes what is committed."""
    p, d = await ready(db, pid)
    if p.repo_url:
        raise Invalid("Das Projekt hat schon ein Repo")
    repo = await github.create_repo(db, name or p.name, private=private, description="", auto_init=False)
    await git.run(d, "remote", "add", "origin", repo["clone_url"])
    p.repo_url = repo["clone_url"]
    if await git.has_commits(d):
        await git.push(d, git.auth_env(await github.account(db)))
    return p


# ---- file tree and preview ----

TEXT_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".json", ".json5", ".jsonl", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".md", ".markdown", ".txt", ".rst", ".log",
    ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1",
    ".sql", ".env", ".example", ".gitignore", ".gitattributes", ".dockerignore",
    ".xml", ".svg", ".csv", ".tsv", ".conf", ".c", ".h", ".cpp", ".hpp",
    ".rs", ".go", ".java", ".kt", ".rb", ".php", ".lua", ".swift",
}

MAX_PREVIEW_BYTES = 2 * 1024 * 1024  # 2 MB for text preview


def resolve_file(d: Path, rel_path: str) -> Path:
    """Safely resolves rel_path inside the project directory d, forbidding traversal and .git."""
    clean = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    target = (d / clean).resolve()
    try:
        rel = target.relative_to(d)
    except ValueError:
        raise Invalid("Ungültiger Dateipfad")
    if ".git" in rel.parts:
        raise Invalid("Zugriff auf .git ist nicht erlaubt")
    return target


def list_dir(d: Path, rel_path: str = "") -> dict:
    target = resolve_file(d, rel_path)
    if not target.exists() or not target.is_dir():
        raise NotFound("Ordner nicht gefunden")

    entries = []
    try:
        items = list(target.iterdir())
    except PermissionError:
        raise Invalid("Keine Berechtigung für diesen Ordner")

    for item in items:
        if item.name == ".git":
            continue
        is_dir = item.is_dir()
        try:
            st = item.stat()
            size = st.st_size if not is_dir else None
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            size = None
            mtime = None

        rel = item.relative_to(d).as_posix()
        ext = item.suffix.lower() if not is_dir else ""
        entries.append({
            "name": item.name,
            "path": rel,
            "is_dir": is_dir,
            "size": size,
            "modified_at": mtime,
            "ext": ext,
        })

    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    curr_rel = target.relative_to(d).as_posix() if target != d else ""
    parent = None
    if target != d:
        p = target.parent.relative_to(d).as_posix()
        parent = "" if p == "." else p

    return {
        "path": curr_rel,
        "parent": parent,
        "entries": entries,
    }


def file_info(d: Path, rel_path: str) -> dict:
    target = resolve_file(d, rel_path)
    if not target.exists() or not target.is_file():
        raise NotFound("Datei nicht gefunden")

    st = target.stat()
    size = st.st_size
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    ext = target.suffix.lower()
    mime, _ = mimetypes.guess_type(target.name)
    if not mime:
        if ext in (".md", ".markdown"):
            mime = "text/markdown"
        elif ext in (".yaml", ".yml"):
            mime = "text/yaml"
        elif ext in (".toml", ".ini", ".cfg", ".env", ".gitignore", ".dockerignore"):
            mime = "text/plain"
        elif ext in (".ts", ".tsx"):
            mime = "text/typescript"
        elif ext in (".py", ".sh", ".ps1", ".bat", ".sql"):
            mime = "text/plain"
        else:
            mime = "application/octet-stream"

    is_text = ext in TEXT_EXTENSIONS or (mime and (mime.startswith("text/") or mime in ("application/json", "application/javascript", "application/xml", "image/svg+xml")))

    content = None
    truncated = False

    if is_text:
        try:
            raw = target.read_bytes()
            if len(raw) > MAX_PREVIEW_BYTES:
                content = raw[:MAX_PREVIEW_BYTES].decode("utf-8", errors="replace")
                truncated = True
            else:
                content = raw.decode("utf-8", errors="replace")
        except Exception:
            is_text = False
            content = None

    return {
        "name": target.name,
        "path": target.relative_to(d).as_posix(),
        "size": size,
        "modified_at": mtime,
        "mime": mime,
        "ext": ext,
        "is_text": is_text,
        "truncated": truncated,
        "content": content,
    }
