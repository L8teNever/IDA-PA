"""Live chat with an agent: streams the answer and stores everything in the database.

The answer runs as a background task on the server: it goes on (and is saved) when the browser switches
the page, closes the app or loses the connection. The browser only follows it (follow) and can come back
later – a reopened chat replays what was written so far and keeps streaming.

Events (NDJSON lines to the browser):
  {"chat": {...}}  first, with the chat id (new chats get one here)
  {"text": "…"}    answer text (streamed)
  {"tool": {id, server, name, input}} / {"tool_result": {id, error, content}}
  {"sys": "…"}     info line (e.g. switched account because of the limit)
  {"approval": {id, tool, input}} / {"approval_done": {id, decision}}   code threads: a request waits for the user
  {"done": {"ok", "error"?, "message_id"}}

Code threads (kind="code") are chats of a code project: the main agent works in the project folder with real
tools (services/code), with the same memory and internal tools as every chat.
"""
import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import aclosing

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import Account, Agent, Chat, ChatMessage, CodeProject, Skill
from . import code as code_area
from . import files as file_svc
from . import providers, runs, wiki
from .code import approvals, git, github
from .claude import runner
from .errors import Invalid, NotFound
from .mcp import identity

log = logging.getLogger("ida.chat")
KEEP_FINISHED = 120  # seconds a finished run stays followable (a browser that comes back just then still gets "done")


def chat_dict(c: Chat) -> dict:
    return {"id": c.id, "agent_id": c.agent_id, "title": c.title, "model": c.model, "account_id": c.account_id,
            "kind": c.kind, "project_id": c.project_id, "permission_mode": c.permission_mode,
            "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat()}


PERMISSION_MODES = ("ask", "edits", "full")


def _transcript(msgs: list[ChatMessage]) -> str:
    lines = []
    for m in msgs:
        names = [f["name"] for f in (m.meta or {}).get("files") or []]
        if m.role in ("user", "assistant") and (m.text or names):
            note = f" [Anhang: {', '.join(names)}]" if names else ""
            lines.append(f"{'Nutzer' if m.role == 'user' else 'Assistent'}: {m.text}{note}")
    return "\n".join(lines)[-12000:]


# chat id -> set when the user presses stop; the running answer ends and what was written so far is kept
_stops: dict[str, asyncio.Event] = {}


def stop(chat_id: str) -> bool:
    ev = _stops.get(chat_id)
    if ev:
        ev.set()
    approvals.cancel_chat(chat_id)  # a request that waits for the user would keep the run alive
    return bool(ev)


async def _until(source: AsyncIterator[dict], stopped: asyncio.Event) -> AsyncIterator[dict]:
    """Passes the run's events on until the stop button is pressed; then the run is closed (stops the CLI)."""
    async with aclosing(source) as events:
        halt = asyncio.ensure_future(stopped.wait())
        try:
            while True:
                nxt = asyncio.ensure_future(events.__anext__())
                await asyncio.wait({nxt, halt}, return_when=asyncio.FIRST_COMPLETED)
                if not nxt.done():
                    nxt.cancel()
                    try:
                        await nxt
                    except (asyncio.CancelledError, StopAsyncIteration):
                        pass
                    return
                try:
                    yield nxt.result()
                except StopAsyncIteration:
                    return
        finally:
            halt.cancel()


class _Live:
    """A running answer: every event is kept, so any number of browsers can follow it – also from the start."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.done = False
        self.cond = asyncio.Condition()
        self.started = utcnow()  # shown as "läuft seit …" when a browser follows it later

    async def push(self, ev: dict) -> None:
        async with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    async def finish(self) -> None:
        async with self.cond:
            self.done = True
            self.cond.notify_all()

    async def follow(self, after: int = 0) -> AsyncIterator[dict]:
        i = max(0, after)
        while True:
            async with self.cond:
                while i >= len(self.events) and not self.done:
                    await self.cond.wait()
                new, finished = self.events[i:], self.done
            for ev in new:
                yield ev
            i += len(new)
            if finished and i >= len(self.events):
                return


_live: dict[str, _Live] = {}
_tasks: set[asyncio.Task] = set()  # keeps the background runs referenced


def running(chat_id: str) -> bool:
    live = _live.get(chat_id)
    return bool(live and not live.done)


def running_since(chat_id: str):
    live = _live.get(chat_id)
    return live.started if live and not live.done else None


async def push(chat_id: str, ev: dict) -> None:
    """An extra event into the running answer (e.g. an approval request), seen by every browser that follows it."""
    live = _live.get(chat_id)
    if live and not live.done:
        await live.push(ev)


def follow(chat_id: str, after: int = 0) -> AsyncIterator[dict] | None:
    """The events of the chat's current (or just finished) answer; None = nothing running."""
    live = _live.get(chat_id)
    return live.follow(after) if live else None


async def start_chat(*, chat_id: str | None, agent_id: str | None, text: str, model: str | None, account_id: str | None,
                     skill_id: str | None, effort: str | None = None, file_ids: list[str] | None = None,
                     project_id: str | None = None, permission_mode: str | None = None) -> dict:
    """Saves the message and starts the answer in the background; returns the chat (follow it with follow()).
    project_id: a new thread of this code project; permission_mode: rights of a code thread (ask | edits | full)."""
    state, order, prompt, skill_id, attachments, info = await _prepare(chat_id=chat_id, agent_id=agent_id, text=text, model=model,
                                                                       account_id=account_id, skill_id=skill_id, file_ids=file_ids or [],
                                                                       project_id=project_id, permission_mode=permission_mode)
    live = _live[state["chat_id"]] = _Live()
    stopped = _stops[state["chat_id"]] = asyncio.Event()

    async def drive():
        try:
            async for ev in _answer(state, order, prompt, skill_id, effort, stopped, attachments):
                await live.push(ev)
        except Exception as e:  # noqa: BLE001 – the browser must always get an end
            log.exception("chat answer %s failed", state["chat_id"])
            await live.push({"done": {"ok": False, "error": f"Fehler: {str(e)[:300] or e.__class__.__name__}"}})
        finally:
            approvals.cancel_chat(state["chat_id"])
            await live.finish()
            wiki.schedule(state["chat_id"])
            if _stops.get(state["chat_id"]) is stopped:
                del _stops[state["chat_id"]]
            asyncio.get_running_loop().call_later(KEEP_FINISHED, lambda: _live.pop(state["chat_id"], None) if _live.get(state["chat_id"]) is live else None)

    task = asyncio.create_task(drive())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return info


async def stream_chat(*, chat_id: str | None, agent_id: str | None, text: str, model: str | None,
                      account_id: str | None, skill_id: str | None, effort: str | None = None,
                      file_ids: list[str] | None = None, project_id: str | None = None,
                      permission_mode: str | None = None) -> AsyncIterator[dict]:
    """Start + follow: {"chat": …} first, then the answer's events. Leaving early does not stop the answer."""
    info = await start_chat(chat_id=chat_id, agent_id=agent_id, text=text, model=model, account_id=account_id,
                            skill_id=skill_id, effort=effort, file_ids=file_ids, project_id=project_id, permission_mode=permission_mode)
    yield {"chat": info}
    async for ev in follow(info["id"]):
        yield ev


async def _prepare(*, chat_id, agent_id, text, model, account_id, skill_id, file_ids, project_id=None, permission_mode=None):
    s = get_settings()
    text = (text or "").strip()
    if chat_id and running(chat_id):
        raise Invalid("Hier läuft noch eine Antwort – warte kurz oder stoppe sie")
    if not text and not skill_id and not file_ids:
        raise Invalid("Nachricht fehlt")
    if permission_mode and permission_mode not in PERMISSION_MODES:
        raise Invalid("Unbekannte Rechte-Stufe")
    async with session_scope() as db:
        project = None
        if chat_id:
            chat = await db.get(Chat, chat_id)
            if not chat:
                raise NotFound("Chat nicht gefunden")
            agent = await db.get(Agent, chat.agent_id)
            if chat.project_id:
                project = await db.get(CodeProject, chat.project_id)
                if not project:
                    raise NotFound("Projekt nicht gefunden")
        elif project_id:  # new thread of a code project: always the main agent (no agent choice in the code area)
            project = await db.get(CodeProject, project_id)
            if not project:
                raise NotFound("Projekt nicht gefunden")
            agent = await runs.main_agent(db)
            chat = Chat(agent_id=agent.id, title=(text or "Neuer Thread")[:60], model=model or agent.model or s.default_model,
                        kind="code", project_id=project.id, permission_mode=permission_mode or "ask")
            db.add(chat)
            await db.flush()
        else:
            agent = await db.get(Agent, agent_id) if agent_id else await runs.main_agent(db)
            if not agent:
                raise NotFound("Agent nicht gefunden")
            chat = Chat(agent_id=agent.id, title=(text or "Neuer Chat")[:60], model=model or agent.model or s.default_model)
            db.add(chat)
            await db.flush()  # the chat needs its id for the files
        code = None
        if project:
            code = await _code_run(db, project, chat, permission_mode, model)
        if model:
            providers.check_model(model)
            chat.model = model
        acc = await runs.account_or_first(db, account_id or chat.account_id or agent.account_id, chat.model)
        chat.account_id = acc.id
        skill = await db.get(Skill, skill_id) if skill_id else None
        if skill_id and not (skill and skill.enabled):
            raise Invalid(f"Skill /{skill_id} gibt es nicht oder er ist aus")
        history = list((await db.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat.id).order_by(ChatMessage.at))).all()) if chat_id else []
        fs = await file_svc.take(db, file_ids, chat.id)
        if fs and not chat_id and not text:
            chat.title = fs[0].name[:60]
        meta = {**({"skill": skill_id} if skill_id else {}), **({"files": [file_svc.info(f) for f in fs]} if fs else {})}
        db.add(ChatMessage(chat_id=chat.id, role="user", text=text, meta=meta))
        chat.updated_at = utcnow()
        await db.flush()
        info = chat_dict(chat)
        state = {"chat_id": chat.id, "agent_id": agent.id, "model": chat.model, "session": chat.session_id if chat.session_account == acc.id else None,
                 "history": _transcript(history), "code": code}
        order = [acc] + ([a for a in await providers.accounts_for(db, chat.model) if a.id != acc.id] if agent.fallback else [])
    # text files go into the message itself; pictures and PDFs go to the model as they are
    prompt = text
    for f in fs:
        if f.kind == "text":
            prompt += f"\n\n<datei name=\"{f.name}\">\n{file_svc.as_text(f)}\n</datei>"
    if fs and not text:
        prompt = ("(Siehe Anhang.)" + prompt) if not skill_id else prompt.strip()
    attachments = [{"kind": f.kind, "mime": f.mime, "path": str(file_svc.path(f)), "name": f.name} for f in fs if f.kind != "text"]
    return state, order, prompt.strip(), skill_id, attachments, info


async def _code_run(db, project: CodeProject, chat: Chat, permission_mode: str | None, model: str | None) -> dict:
    """What a code thread's run needs: the project folder, its rights and the GitHub identity for git."""
    from .code import projects
    await code_area.require(db)
    if project.status != "ready":
        raise Invalid("Das Projekt wird noch geklont" if project.status == "cloning" else f"Das Projekt ist nicht bereit: {project.status_text}")
    if permission_mode:
        chat.permission_mode = permission_mode
    if providers.of_model(model or chat.model) != "claude":
        raise Invalid("Im Code-Bereich laufen nur Claude-Modelle")
    path = projects.path_of(project)
    try:
        branch = (await git.run(path, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    except Invalid:
        branch = project.default_branch
    project.updated_at = utcnow()
    return {"path": path, "mode": chat.permission_mode, "name": project.name, "repo_url": project.repo_url, "branch": branch,
            "env": git.auth_env(await github.account(db))}


async def _answer(state: dict, order: list[Account], text: str, skill_id: str | None, effort: str | None,
                  stopped: asyncio.Event, attachments: list[dict] | None = None) -> AsyncIterator[dict]:
    prompt_text = text or "(Wende den Skill auf das bisherige Gespräch bzw. deine letzte Antwort an.)"
    t0 = time.monotonic()  # how long the answer took (shown under it)
    parts: list[dict] = []
    tools: dict[str, dict] = {}
    result: dict = {"ok": False, "result": "Keine Antwort"}
    used: Account | None = None
    for i, acc in enumerate(order):
        resume = state["session"] if i == 0 else None
        prompt = prompt_text
        if not resume and state["history"]:  # new session (or other account): carry the conversation over
            prompt = f"Bisheriger Gesprächsverlauf (setze das Gespräch nahtlos fort):\n{state['history']}\n\nNeue Nachricht des Nutzers:\n{prompt_text}"
        async with session_scope() as db:
            agent = await db.get(Agent, state["agent_id"])
            skill = await db.get(Skill, skill_id) if skill_id else None
            p = await runs.prepare(db, agent, prompt, model=state["model"], account=acc, interactive=True, forced_skill=skill,
                                   session_id=resume, persist=True, effort=effort, code=state.get("code"), chat_id=state["chat_id"])
        p.spec.attachments = list(attachments or [])
        if state.get("code"):  # tools the user allowed for good in this thread
            p.spec.allow += approvals.always(state["chat_id"])
        used, streamed = acc, False
        try:
            async for ev in _until(runner.stream(p.spec), stopped):
                t = ev["type"]
                if t == "text":
                    streamed = True
                    if parts and parts[-1]["t"] == "text":
                        parts[-1]["text"] += ev["text"]
                    else:
                        parts.append({"t": "text", "text": ev["text"]})
                    yield {"text": ev["text"]}
                elif t == "tool":
                    streamed = True
                    part = {"t": "tool", "id": ev["id"], "server": ev["server"], "name": ev["name"], "input": ev["input"]}
                    tools[ev["id"]] = part
                    parts.append(part)
                    yield {"tool": {k: part[k] for k in ("id", "server", "name", "input")}}
                elif t == "tool_result":
                    if ev["id"] in tools:
                        tools[ev["id"]].update(out=ev["content"][:2000], error=ev["error"])
                    yield {"tool_result": {"id": ev["id"], "error": ev["error"], "content": ev["content"][:2000]}}
                elif t == "done":
                    result = ev
        finally:
            identity.revoke(p.token)
        if stopped.is_set():
            result = {"ok": False, "result": "Von dir gestoppt", "stopped": True}
            break
        limit = not result["ok"] and bool(runner.LIMIT_RE.search(result["result"]))
        if result["ok"] or streamed or not limit or i == len(order) - 1:
            break
        yield {"sys": f"{acc.name} ist am Limit – weiter mit {order[i + 1].name}"}

    answer = "".join(p["text"] for p in parts if p["t"] == "text").strip()
    ms = int((time.monotonic() - t0) * 1000)
    async with session_scope() as db:
        chat = await db.get(Chat, state["chat_id"])
        meta = {"model": state["model"], "account": used.name if used else None, "ms": ms}
        if result.get("stopped"):
            meta["stopped"] = True
            chat.session_id = None  # the cut-off turn is not in Claude's session – the next message carries the history
        elif not result["ok"]:
            meta["error"] = result["result"][:800]
        msg = ChatMessage(chat_id=chat.id, role="assistant", text=answer or ("" if not result["ok"] else result["result"]), parts=parts, meta=meta)
        db.add(msg)
        if result["ok"] and result.get("session_id") and used:
            chat.session_id, chat.session_account = result["session_id"], used.id
        if used:
            chat.account_id = used.id
        chat.updated_at = utcnow()
        await db.flush()
        mid = msg.id
    yield {"done": {"ok": result["ok"], "message_id": mid, "ms": ms, **({"stopped": True} if result.get("stopped") else {} if result["ok"] else {"error": result["result"][:800]})}}
