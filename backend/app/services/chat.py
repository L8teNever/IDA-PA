"""Live chat with an agent: streams the answer and stores everything in the database.

Events (NDJSON lines to the browser):
  {"chat": {...}}  first, with the chat id (new chats get one here)
  {"text": "…"}    answer text (streamed)
  {"tool": {id, server, name, input}} / {"tool_result": {id, error, content}}
  {"sys": "…"}     info line (e.g. switched account because of the limit)
  {"done": {"ok", "error"?, "message_id"}}
"""
import logging
from collections.abc import AsyncIterator

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import Account, Agent, Chat, ChatMessage, Skill
from . import runs
from .claude import runner
from .errors import Invalid, NotFound
from .mcp import identity

log = logging.getLogger("ida.chat")


def chat_dict(c: Chat) -> dict:
    return {"id": c.id, "agent_id": c.agent_id, "title": c.title, "model": c.model, "account_id": c.account_id,
            "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat()}


def _transcript(msgs: list[ChatMessage]) -> str:
    lines = []
    for m in msgs:
        if m.role in ("user", "assistant") and m.text:
            lines.append(f"{'Nutzer' if m.role == 'user' else 'Assistent'}: {m.text}")
    return "\n".join(lines)[-12000:]


async def stream_chat(*, chat_id: str | None, agent_id: str | None, text: str, model: str | None,
                      account_id: str | None, skill_id: str | None) -> AsyncIterator[dict]:
    s = get_settings()
    text = (text or "").strip()
    async with session_scope() as db:
        if chat_id:
            chat = await db.get(Chat, chat_id)
            if not chat:
                raise NotFound("Chat nicht gefunden")
            agent = await db.get(Agent, chat.agent_id)
        else:
            agent = await db.get(Agent, agent_id) if agent_id else await runs.main_agent(db)
            if not agent:
                raise NotFound("Agent nicht gefunden")
            chat = Chat(agent_id=agent.id, title=(text or "Neuer Chat")[:60], model=model or agent.model or s.default_model)
            db.add(chat)
        if model:
            if model not in s.model_list:
                raise Invalid("Unbekanntes Modell")
            chat.model = model
        acc = await runs.account_or_first(db, account_id or chat.account_id or agent.account_id)
        chat.account_id = acc.id
        skill = await db.get(Skill, skill_id) if skill_id else None
        if skill_id and not (skill and skill.enabled):
            raise Invalid(f"Skill /{skill_id} gibt es nicht oder er ist aus")
        history = list((await db.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat.id).order_by(ChatMessage.at))).all()) if chat_id else []
        db.add(ChatMessage(chat_id=chat.id, role="user", text=text, meta={"skill": skill_id} if skill_id else {}))
        chat.updated_at = utcnow()
        await db.flush()
        info = chat_dict(chat)
        state = {"chat_id": chat.id, "agent_id": agent.id, "model": chat.model, "session": chat.session_id if chat.session_account == acc.id else None,
                 "history": _transcript(history)}
        order = [acc] + ([a for a in await runs.accounts(db) if a.id != acc.id] if agent.fallback else [])
    yield {"chat": info}

    prompt_text = text or "(Wende den Skill auf das bisherige Gespräch bzw. deine letzte Antwort an.)"
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
                                   session_id=resume, persist=True)
        used, streamed = acc, False
        try:
            async for ev in runner.stream(p.spec):
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
        limit = not result["ok"] and bool(runner.LIMIT_RE.search(result["result"]))
        if result["ok"] or streamed or not limit or i == len(order) - 1:
            break
        yield {"sys": f"{acc.name} ist am Limit – weiter mit {order[i + 1].name}"}

    answer = "".join(p["text"] for p in parts if p["t"] == "text").strip()
    async with session_scope() as db:
        chat = await db.get(Chat, state["chat_id"])
        meta = {"model": state["model"], "account": used.name if used else None}
        if not result["ok"]:
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
    yield {"done": {"ok": result["ok"], "message_id": mid, **({} if result["ok"] else {"error": result["result"][:800]})}}
