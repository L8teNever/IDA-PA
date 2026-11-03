"""Code area: on/off switch, projects (git repos), their threads, git actions and approvals.
Threads themselves are chats: messages go through POST /chat (with project_id for a new thread), /chats/{id}/live, /stop."""
import mimetypes
import re

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import select

from ..models import Chat, ChatMessage
from ..db import session_scope, utcnow
from ..schemas import ApprovalAnswer, CodeSettings, CommitMessageSuggest, Draft, QueueItem, GitBranch, GitCommit, ProjectCreate, ProjectUpdate, Publish, PullRequest, ThreadUpdate
from ..services import chat as chat_service
from ..services import chat_queue
from ..services import code as code_area
from ..services import settings_store
from ..services.code import approvals, commit_msg, git, github, projects
from ..services.errors import Invalid, NotFound
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/code", tags=["code"])


@router.get("/settings")
async def code_settings(db: DB):
    return {"enabled": await code_area.enabled(db), "github": await github.status(db)}


@router.put("/settings")
async def set_code_settings(body: CodeSettings, db: DB):
    await settings_store.put(db, code_area.ENABLED_KEY, body.enabled)
    return {"enabled": body.enabled}


# ---- projects ----

@router.get("/projects")
async def list_projects(db: DB):
    await code_area.require(db)
    return [ser.code_project(p, n, r) for p, n, r in await projects.listing(db)]


@router.post("/projects")
async def create_project(body: ProjectCreate, db: DB):
    await code_area.require(db)
    url, name = body.repo_url, body.name
    if body.new_repo:
        repo = await github.create_repo(db, body.new_repo.name, private=body.new_repo.private, description=body.new_repo.description)
        url, name = repo["clone_url"], name or repo["name"]
    p = await projects.create(db, name, url)
    try:
        from ..services import wiki_sync
        wiki_sync.enqueue_project(p.id)
    except Exception:
        pass
    return ser.code_project(p, 0, 0)


async def _project(db, pid: str):
    await code_area.require(db)
    return await projects.ready(db, pid)


@router.get("/projects/{pid}")
async def get_project(pid: str, db: DB):
    await code_area.require(db)
    p = await projects.get(db, pid)
    out = ser.code_project(p)
    if p.status == "ready":
        try:
            out["git"] = await git.status(projects.path_of(p))
        except Invalid as e:
            out["git_error"] = str(e)
    return out


@router.patch("/projects/{pid}")
async def rename_project(pid: str, body: ProjectUpdate, db: DB):
    await code_area.require(db)
    p = await projects.get(db, pid)
    if not body.name.strip():
        raise Invalid("Name fehlt")
    p.name = body.name.strip()[:80]
    try:
        from ..services import wiki_sync
        wiki_sync.enqueue_project(p.id)
    except Exception:
        pass
    return ser.code_project(p)


@router.delete("/projects/{pid}")
async def delete_project(pid: str, db: DB):
    await code_area.require(db)
    await projects.delete(db, pid)
    await settings_store.delete(db, f"code_draft:p-{pid}")
    try:
        from ..services import wiki_sync
        wiki_sync.enqueue_project_delete(pid)
    except Exception:
        pass
    return {"ok": True}


@router.post("/projects/{pid}/publish")
async def publish(pid: str, body: Publish, db: DB):
    """Local project -> new GitHub repo (origin) and push."""
    await code_area.require(db)
    return ser.code_project(await projects.publish(db, pid, body.name, body.private))


# ---- files and preview ----

@router.get("/projects/{pid}/files")
async def project_files(pid: str, db: DB, path: str = ""):
    _, d = await _project(db, pid)
    return projects.list_dir(d, path)


@router.get("/projects/{pid}/file")
async def project_file(pid: str, db: DB, path: str):
    _, d = await _project(db, pid)
    return projects.file_info(d, path)


@router.get("/projects/{pid}/raw/{file_path:path}")
async def project_raw_file(pid: str, file_path: str, db: DB):
    _, d = await _project(db, pid)
    target = projects.resolve_file(d, file_path)
    if not target.exists() or not target.is_file():
        raise NotFound("Datei nicht gefunden")

    ext = target.suffix.lower()
    mime, _ = mimetypes.guess_type(target.name)
    if ext in (".html", ".htm"):
        mime = "text/html; charset=utf-8"
    elif ext in (".js", ".mjs"):
        mime = "application/javascript; charset=utf-8"
    elif ext == ".css":
        mime = "text/css; charset=utf-8"
    elif ext == ".svg":
        mime = "image/svg+xml"
    elif ext in (".md", ".markdown"):
        mime = "text/markdown; charset=utf-8"
    elif ext == ".json":
        mime = "application/json; charset=utf-8"
    elif not mime:
        mime = "text/plain; charset=utf-8" if ext in projects.TEXT_EXTENSIONS else "application/octet-stream"

    return FileResponse(target, media_type=mime, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"})


# ---- threads ----

@router.get("/projects/{pid}/threads")
async def threads(pid: str, db: DB):
    await code_area.require(db)
    await projects.get(db, pid)
    rows = (await db.scalars(select(Chat).where(Chat.project_id == pid).order_by(Chat.updated_at.desc()))).all()
    out = []
    for c in rows:
        last = await db.scalar(select(ChatMessage).where(ChatMessage.chat_id == c.id, ChatMessage.role != "system").order_by(ChatMessage.at.desc()).limit(1))
        out.append({**ser.chat(c, last), "running": chat_service.running(c.id), "waiting": len(approvals.waiting(c.id))})
    return out


@router.patch("/threads/{cid}")
async def update_thread(cid: str, body: ThreadUpdate, db: DB):
    """Title and rights of a thread (new rights apply from the next message on)."""
    await code_area.require(db)
    c = await db.get(Chat, cid)
    if not c or c.kind != "code":
        raise NotFound("Thread nicht gefunden")
    if body.title is not None:
        if not body.title.strip():
            raise Invalid("Titel fehlt")
        c.title = body.title.strip()[:120]
    if body.permission_mode:
        c.permission_mode = body.permission_mode
    return ser.chat(c)


# ---- drafts: unsent text in the input field, kept on the server so every device sees the same ----
# key "t-<thread id>" for a thread, "p-<project id>" for a new thread in a project

_DRAFT_KEY = re.compile(r"[tp]-[A-Za-z0-9_-]{1,32}")


def _draft_key(key: str) -> str:
    if not _DRAFT_KEY.fullmatch(key):
        raise NotFound("Entwurf nicht gefunden")
    return f"code_draft:{key}"


@router.get("/drafts/{key}")
async def get_draft(key: str, db: DB):
    await code_area.require(db)
    d = await settings_store.get(db, _draft_key(key)) or {}
    return {"text": d.get("text", ""), "at": d.get("at")}


@router.put("/drafts/{key}")
async def put_draft(key: str, body: Draft, db: DB):
    """Empty text removes the draft (sent or cleared)."""
    await code_area.require(db)
    k = _draft_key(key)
    if body.text.strip():
        at = ser.iso(utcnow())
        await settings_store.put(db, k, {"text": body.text, "at": at})
        return {"text": body.text, "at": at}
    await settings_store.delete(db, k)
    return {"text": "", "at": None}


# ---- queue: messages written while the AI still works; the server sends them one after another ----

async def _thread(db, chat_id: str) -> Chat:
    await code_area.require(db)
    c = await db.get(Chat, chat_id)
    if not c or c.kind != "code":
        raise NotFound("Thread nicht gefunden")
    return c


async def _kick(db, chat_id: str) -> None:
    """Nothing running: the first waiting message goes out right away."""
    await db.commit()  # the queue must be saved before the run takes from it
    if chat_service.running(chat_id):
        return
    item = await chat_queue.take(chat_id, user_stopped=False)
    if item:
        try:
            await chat_service.start_chat(chat_id=chat_id, agent_id=None, text=item.get("text") or "", model=item.get("model"),
                                          account_id=item.get("account_id"), skill_id=None, effort=item.get("effort"),
                                          file_ids=item.get("files") or [])
        except Exception as e:
            await chat_queue.put_back(chat_id, item, str(e) or e.__class__.__name__)
            raise


async def _queue_out(db, chat_id: str) -> dict:
    await db.commit()
    async with session_scope() as fresh:  # the run may have taken from it in its own session
        return {**await chat_queue.get(fresh, chat_id), "running": chat_service.running(chat_id)}


@router.get("/queue/{chat_id}")
async def get_queue(chat_id: str, db: DB):
    await _thread(db, chat_id)
    return await _queue_out(db, chat_id)


@router.post("/queue/{chat_id}")
async def add_to_queue(chat_id: str, body: QueueItem, db: DB):
    """Waits until the running answer is done; if none runs (just finished), it goes out right away."""
    await _thread(db, chat_id)
    if not body.text.strip() and not body.files:
        raise Invalid("Nachricht fehlt")
    async with chat_queue.lock(chat_id):
        await chat_queue.add(db, chat_id, body.model_dump())
        await _kick(db, chat_id)
    return await _queue_out(db, chat_id)


@router.delete("/queue/{chat_id}/{item_id}")
async def remove_from_queue(chat_id: str, item_id: str, db: DB):
    await _thread(db, chat_id)
    async with chat_queue.lock(chat_id):
        await chat_queue.remove(db, chat_id, item_id)
    return await _queue_out(db, chat_id)


@router.post("/queue/{chat_id}/{item_id}/now")
async def send_now(chat_id: str, item_id: str, db: DB):
    """"Jetzt senden": stops the running answer (what was written stays) and sends this message next."""
    await _thread(db, chat_id)
    async with chat_queue.lock(chat_id):
        if not await chat_queue.to_front(db, chat_id, item_id):
            raise NotFound("Diese Nachricht wartet nicht mehr")
        if chat_service.running(chat_id):
            await db.commit()
            chat_queue.steer(chat_id)
            chat_service.stop(chat_id)
        else:
            await _kick(db, chat_id)
    return await _queue_out(db, chat_id)


# ---- git ----

async def _env(db) -> dict:
    return git.auth_env(await github.account(db))


@router.get("/projects/{pid}/git")
async def git_status(pid: str, db: DB):
    _, d = await _project(db, pid)
    return {**await git.status(d), "branches": await git.branches(d), "github": (await github.status(db))["login"]}


@router.get("/projects/{pid}/diff")
async def git_diff(pid: str, db: DB, file: str | None = None):
    _, d = await _project(db, pid)
    return await git.diff(d, file)


@router.post("/projects/{pid}/commit")
async def git_commit(pid: str, body: GitCommit, db: DB):
    _, d = await _project(db, pid)
    env = await _env(db)
    out = await git.commit(d, body.message, env)
    if body.push:
        out["pushed"] = (await git.push(d, env))["branch"]
    return out


@router.post("/projects/{pid}/commit-message")
async def suggest_commit_message(pid: str, body: CommitMessageSuggest, db: DB):
    """Short commit message from the current diff, with the chosen model (no tools)."""
    _, d = await _project(db, pid)
    return await commit_msg.suggest(db, d, model=body.model, file=body.file)


@router.post("/projects/{pid}/push")
async def git_push(pid: str, db: DB):
    _, d = await _project(db, pid)
    await github.need_token(db)
    return await git.push(d, await _env(db))


@router.post("/projects/{pid}/branch")
async def git_branch(pid: str, body: GitBranch, db: DB):
    _, d = await _project(db, pid)
    for c in (await db.scalars(select(Chat.id).where(Chat.project_id == pid))).all():
        if chat_service.running(c):
            raise Invalid("Gerade arbeitet ein Thread in diesem Projekt – erst warten oder stoppen")
    await git.switch(d, body.name, body.create)
    return await git.status(d)


@router.post("/projects/{pid}/pr")
async def pull_request(pid: str, body: PullRequest, db: DB):
    """Pushes the current branch and opens a pull request against the default branch."""
    p, d = await _project(db, pid)
    st = await git.status(d)
    name = git.full_name(st["remote"] or p.repo_url)
    if not name:
        raise Invalid("Pull Requests gehen nur bei Projekten mit GitHub-Repo")
    base = (body.base or p.default_branch).strip()
    if st["branch"] == base:
        raise Invalid(f"Du bist auf {base} – lege erst einen eigenen Branch an")
    if not body.title.strip():
        raise Invalid("Titel fehlt")
    await github.need_token(db)
    await git.push(d, await _env(db))
    return await github.create_pr(db, name, st["branch"], base, body.title.strip(), body.body)


# ---- GitHub Actions ----

async def _repo(db, pid: str) -> str:
    p, d = await _project(db, pid)
    st = await git.status(d)
    name = git.full_name(st["remote"] or p.repo_url)
    if not name:
        raise Invalid("Actions gibt es nur bei Projekten mit GitHub-Repo")
    return name


@router.get("/projects/{pid}/actions/runs")
async def actions_runs(pid: str, db: DB, branch: str | None = None):
    return await github.runs(db, await _repo(db, pid), branch)


@router.get("/projects/{pid}/actions/runs/{run_id}")
async def actions_run(pid: str, run_id: int, db: DB):
    return await github.run_detail(db, await _repo(db, pid), run_id)


@router.get("/projects/{pid}/actions/jobs/{job_id}/log")
async def actions_log(pid: str, job_id: int, db: DB):
    return await github.job_log(db, await _repo(db, pid), job_id)


@router.post("/projects/{pid}/actions/runs/{run_id}/{action}")
async def actions_do(pid: str, run_id: int, action: str, db: DB):
    await github.run_action(db, await _repo(db, pid), run_id, action)
    return {"ok": True}


# ---- approvals ("Nachfragen") ----

@router.get("/approvals")
async def waiting(chat_id: str):
    return approvals.waiting(chat_id)


@router.post("/approvals/{aid}")
async def answer(aid: str, body: ApprovalAnswer):
    return approvals.answer(aid, body.decision)
