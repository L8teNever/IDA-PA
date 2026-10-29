"""Chats in the wiki (XWiki): every chat is a page under Ida › Chats, written again after every answer.

Off while WIKI_URL is empty. The page name is the chat id, so a chat always lands on the same page; a chat
deleted in IDA stays in the wiki (archive). On start, all chats changed since the last successful run are
written once more – e.g. while the wiki was down.
"""
import asyncio
import logging
from datetime import datetime
from xml.sax.saxutils import escape

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import Agent, Chat, ChatMessage, CodeProject
from . import settings_store
from .schedule import tz

log = logging.getLogger("ida.wiki")

SPACE = ("Ida", "Chats")
SYNCED_KEY = "wiki_synced_until"
DELAY_S = 3  # an answer often ends with several quick writes (title, message) – write the page once afterwards

_due: set[str] = set()
_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()


def enabled() -> bool:
    s = get_settings()
    return bool(s.wiki_url and s.wiki_user)


def client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(base_url=s.wiki_url.rstrip("/"), auth=(s.wiki_user, s.wiki_password), timeout=30)


def page_path(spaces: tuple[str, ...], name: str) -> str:
    return "/rest/wikis/xwiki/" + "/".join(f"spaces/{sp}" for sp in spaces) + f"/pages/{name}"


async def put_page(c: httpx.AsyncClient, spaces: tuple[str, ...], name: str, title: str, content: str, syntax: str = "markdown/1.2") -> None:
    body = (f'<page xmlns="http://www.xwiki.org"><title>{escape(title)}</title>'
            f"<syntax>{syntax}</syntax><content>{escape(content)}</content></page>")
    r = await c.put(page_path(spaces, name), content=body.encode("utf8"), headers={"Content-Type": "application/xml"})
    if r.status_code not in (200, 201, 202, 304):
        raise RuntimeError(f"Wiki antwortet {r.status_code}: {r.text[:200]}")


def _when(d: datetime) -> str:
    return d.astimezone(tz()).strftime("%d.%m.%Y, %H:%M")


def _safe(text: str) -> str:
    # no wiki macros out of chat text ({{velocity}} …) – a chat may quote anything from a web page
    return (text or "").replace("{{", "{​{").strip()


def chat_markdown(chat: Chat, agent: Agent | None, project: CodeProject | None, msgs: list[ChatMessage]) -> str:
    s = get_settings()
    who = agent.name if agent else "Ida"
    head = [f"**Agent:** {who}", f"**Modell:** {chat.model}", f"**Begonnen:** {_when(chat.created_at)}", f"**Zuletzt:** {_when(chat.updated_at)}"]
    if project:
        head.insert(1, f"**Code-Projekt:** {project.name}")
    if s.ida_public_url:
        path = f"/code/{project.id}/{chat.id}" if project else f"/chat/{chat.id}"
        head.append(f"[In IDA öffnen]({s.ida_public_url.rstrip('/')}{path})")
    out = [" · ".join(head), ""]
    for m in msgs:
        if m.role not in ("user", "assistant"):
            continue
        tools = sorted({p.get("name") or "" for p in (m.parts or []) if p.get("t") == "tool"} - {""})
        error = (m.meta or {}).get("error")
        if not (m.text or "").strip() and not tools and not error:
            continue
        out.append(f"### {'Du' if m.role == 'user' else who} · {_when(m.at)}")
        out.append("")
        if tools:
            out += [f"_Werkzeuge: {', '.join(tools)}_", ""]
        if m.text.strip():
            out += [_safe(m.text), ""]
        if error:
            out += [f"> Fehler: {_safe(error)}", ""]
    return "\n".join(out).rstrip() + "\n"


async def sync_chat(chat_id: str, c: httpx.AsyncClient | None = None) -> bool:
    """Writes one chat; False if it does not exist (any more)."""
    async with session_scope() as db:
        chat = await db.get(Chat, chat_id)
        if not chat:
            return False
        agent = await db.get(Agent, chat.agent_id)
        project = await db.get(CodeProject, chat.project_id) if chat.project_id else None
        msgs = list((await db.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat_id).order_by(ChatMessage.at))).all())
        title = (f"Code · {project.name} · " if project else "") + (chat.title or "Chat")
        content = chat_markdown(chat, agent, project, msgs)
    if c is not None:
        await put_page(c, SPACE, chat_id, title, content)
    else:
        async with client() as own:
            await put_page(own, SPACE, chat_id, title, content)
    return True


def schedule(chat_id: str) -> None:
    """After an answer: write the chat in a moment (several calls in a row = one write)."""
    if not enabled() or chat_id in _due:
        return
    _due.add(chat_id)

    async def run():
        await asyncio.sleep(DELAY_S)
        _due.discard(chat_id)
        async with _lock:
            try:
                await sync_chat(chat_id)
            except Exception as e:  # noqa: BLE001 – the wiki must never break a chat
                log.warning("Chat %s nicht ins Wiki geschrieben: %s", chat_id, e)

    t = asyncio.get_running_loop().create_task(run())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def catch_up() -> int:
    """Writes every chat changed since the last complete run; returns how many."""
    if not enabled():
        return 0
    started = utcnow()
    async with session_scope() as db:
        since = await settings_store.get(db, SYNCED_KEY)
        q = select(Chat.id).order_by(Chat.updated_at)
        if since:
            q = q.where(Chat.updated_at >= datetime.fromisoformat(since))
        ids = list((await db.scalars(q)).all())
    n = 0
    async with _lock, client() as c:
        for cid in ids:
            if await sync_chat(cid, c):
                n += 1
    async with session_scope() as db:
        await settings_store.put(db, SYNCED_KEY, started.isoformat())
    return n


def start() -> None:
    """At app start: catch up in the background (the wiki may still be booting – then try again later)."""
    if not enabled():
        return

    async def run():
        for wait in (20, 60, 300, 900):
            await asyncio.sleep(wait)
            try:
                n = await catch_up()
                log.info("Wiki: %d Chats geschrieben", n)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("Wiki nicht erreichbar (%s) – neuer Versuch später", e)

    t = asyncio.get_running_loop().create_task(run())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
