"""Code area: on/off switch, projects (git repos), their threads, git actions and approvals.
Threads themselves are chats: messages go through POST /chat (with project_id for a new thread), /chats/{id}/live, /stop."""
from fastapi import APIRouter
from sqlalchemy import select

from ..models import Chat, ChatMessage
from ..schemas import ApprovalAnswer, CodeSettings, GitBranch, GitCommit, ProjectCreate, ProjectUpdate, Publish, PullRequest, ThreadUpdate
from ..services import chat as chat_service
from ..services import code as code_area
from ..services import settings_store
from ..services.code import approvals, git, github, projects
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
    return ser.code_project(p)


@router.delete("/projects/{pid}")
async def delete_project(pid: str, db: DB):
    await code_area.require(db)
    await projects.delete(db, pid)
    return {"ok": True}


@router.post("/projects/{pid}/publish")
async def publish(pid: str, body: Publish, db: DB):
    """Local project -> new GitHub repo (origin) and push."""
    await code_area.require(db)
    return ser.code_project(await projects.publish(db, pid, body.name, body.private))


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


# ---- approvals ("Nachfragen") ----

@router.get("/approvals")
async def waiting(chat_id: str):
    return approvals.waiting(chat_id)


@router.post("/approvals/{aid}")
async def answer(aid: str, body: ApprovalAnswer):
    return approvals.answer(aid, body.decision)
