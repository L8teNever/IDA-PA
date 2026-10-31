"""Echtzeit-Spiegel von Agents, Chats und Code-Projekten ins Wiki (`ki/`).

Fire-and-forget: Fehler bremsen IDA nicht. Beim Start und alle ~15 Min. Reconcile gegen den Wiki-Stand.
"""
import asyncio
import base64
import logging
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import Agent, Chat, ChatFile, ChatMessage, CodeProject
from . import files as file_svc
from . import wiki_client as wc

log = logging.getLogger("ida.wiki_sync")

_queue: asyncio.Queue | None = None
_worker: asyncio.Task | None = None
_started_reconcile = False


def enabled() -> bool:
    return wc.enabled()


def _q() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def _work() -> None:
    q = _q()
    while True:
        kind, payload = await q.get()
        try:
            if kind == "chat":
                await _push_chat(payload)
            elif kind == "chat_delete":
                await wc.request("DELETE", f"/api/v1/internal/sync/chat/{payload}", by="system:sync")
            elif kind == "agent":
                await _push_agent(payload)
            elif kind == "agent_delete":
                await wc.request("DELETE", f"/api/v1/internal/sync/agent/{payload}", by="system:sync")
            elif kind == "project":
                await _push_project(payload)
            elif kind == "project_delete":
                await wc.request("DELETE", f"/api/v1/internal/sync/project/{payload}", by="system:sync")
            elif kind == "reconcile":
                await _reconcile()
        except wc.WikiOffline:
            log.warning("wiki sync skipped (%s): Wiki offline", kind)
        except Exception:
            log.exception("wiki sync %s failed", kind)
        finally:
            q.task_done()


def start() -> None:
    global _worker, _started_reconcile
    if not enabled():
        return
    if _worker is None or _worker.done():
        _worker = asyncio.get_running_loop().create_task(_work())
    if not _started_reconcile:
        _started_reconcile = True
        enqueue_reconcile()


async def stop() -> None:
    global _worker, _queue, _started_reconcile
    if _worker:
        _worker.cancel()
        try:
            await _worker
        except (asyncio.CancelledError, Exception):
            pass
    _worker, _queue, _started_reconcile = None, None, False


def _put(kind: str, payload) -> None:
    if not enabled():
        return
    start()
    _q().put_nowait((kind, payload))


def enqueue_chat(chat_id: str) -> None:
    _put("chat", chat_id)


def enqueue_chat_delete(chat_id: str) -> None:
    _put("chat_delete", chat_id)


def enqueue_agent(agent_id: str) -> None:
    _put("agent", agent_id)


def enqueue_agent_delete(agent_id: str) -> None:
    _put("agent_delete", agent_id)


def enqueue_project(project_id: str) -> None:
    _put("project", project_id)


def enqueue_project_delete(project_id: str) -> None:
    _put("project_delete", project_id)


def enqueue_reconcile() -> None:
    _put("reconcile", None)


def _iso(dt) -> str:
    return dt.isoformat() if dt else ""


async def _push_agent(agent_id: str) -> None:
    async with session_scope() as db:
        a = await db.get(Agent, agent_id)
        if not a:
            return
        payload = {
            "id": a.id,
            "name": a.name,
            "description": a.description or a.role or "",
            "role": a.role or "",
            "model": a.model or "",
            "active": bool(a.enabled),
            "updated_at": _iso(a.updated_at),
            "ida_url": "/",
        }
    await wc.request("POST", "/api/v1/internal/sync/agent", by="system:sync", json=payload)


async def _push_project(project_id: str) -> None:
    async with session_scope() as db:
        p = await db.get(CodeProject, project_id)
        if not p:
            return
        payload = {
            "id": p.id,
            "name": p.name,
            "agent_id": "main",
            "description": p.status_text or "",
            "git_branch": p.default_branch or "",
            "git_remote": p.repo_url or "",
            "updated_at": _iso(p.updated_at),
            "ida_url": f"/code/{p.id}",
        }
    await wc.request("POST", "/api/v1/internal/sync/project", by="system:sync", json=payload)


async def _file_payload(db, f: ChatFile) -> dict | None:
    p = file_svc.path(f)
    if not p.exists() or f.size > 8 * 1024 * 1024:
        return {"id": f.id, "name": f.name, "mime": f.mime, "size": f.size}
    raw = await asyncio.to_thread(p.read_bytes)
    return {
        "id": f.id, "name": f.name, "mime": f.mime, "size": f.size,
        "data_b64": base64.b64encode(raw).decode("ascii"),
    }


async def _push_chat(chat_id: str) -> None:
    async with session_scope() as db:
        c = await db.get(Chat, chat_id)
        if not c:
            return
        agent = await db.get(Agent, c.agent_id)
        msgs = (await db.scalars(
            select(ChatMessage).where(ChatMessage.chat_id == chat_id).order_by(ChatMessage.at)
        )).all()
        files = (await db.scalars(select(ChatFile).where(ChatFile.chat_id == chat_id))).all()
        project = await db.get(CodeProject, c.project_id) if c.project_id else None

        messages = []
        for m in msgs:
            tool_calls = []
            for p in (m.parts or []):
                if p.get("t") == "tool":
                    tool_calls.append({
                        "name": p.get("name") or "tool",
                        "args": p.get("input") or {},
                        "result": (p.get("out") or "")[:3000],
                    })
            messages.append({
                "id": m.id,
                "role": m.role,
                "content": m.text or "",
                "created_at": _iso(m.at),
                "tool_calls": tool_calls,
            })

        file_rows = []
        for f in files:
            row = await _file_payload(db, f)
            if row:
                file_rows.append(row)

        kind = "code" if c.kind == "code" else "chat"
        payload = {
            "id": c.id,
            "kind": kind,
            "agent_id": c.agent_id,
            "agent_name": agent.name if agent else c.agent_id,
            "title": c.title,
            "created_at": _iso(c.created_at),
            "updated_at": _iso(c.updated_at),
            "ida_url": f"/code/{c.project_id}/{c.id}" if kind == "code" and c.project_id else f"/chat/{c.id}",
            "messages": messages,
            "files": file_rows,
        }
        if project:
            payload["project_id"] = project.id
            payload["project_name"] = project.name

    await wc.request("POST", "/api/v1/internal/sync/chat", by="system:sync", json=payload, timeout=60)


async def _reconcile() -> None:
    """Fehlende/veraltete Spiegel nachziehen; im Wiki verwaiste Chats löschen."""
    state = await wc.request("GET", "/api/v1/internal/sync/state", by="system:sync")
    wiki_chats = state.get("chats") or {}
    wiki_agents = state.get("agents") or {}
    wiki_projects = state.get("projects") or {}

    async with session_scope() as db:
        agents = (await db.scalars(select(Agent))).all()
        projects = (await db.scalars(select(CodeProject))).all()
        chats = (await db.scalars(select(Chat))).all()
        agent_ids = {a.id for a in agents}
        project_ids = {p.id for p in projects}
        chat_map = {c.id: c for c in chats}

    for a in agents:
        known = wiki_agents.get(a.id, "")
        if known != _iso(a.updated_at):
            enqueue_agent(a.id)
    for aid in list(wiki_agents):
        if aid not in agent_ids:
            enqueue_agent_delete(aid)

    for p in projects:
        known = wiki_projects.get(p.id, "")
        if known != _iso(p.updated_at):
            enqueue_project(p.id)
    for pid in list(wiki_projects):
        if pid not in project_ids:
            enqueue_project_delete(pid)

    for c in chat_map.values():
        info = wiki_chats.get(c.id) or {}
        known = info.get("updated_at") if isinstance(info, dict) else str(info or "")
        if known != _iso(c.updated_at):
            enqueue_chat(c.id)
    for cid in list(wiki_chats):
        if cid not in chat_map:
            enqueue_chat_delete(cid)
