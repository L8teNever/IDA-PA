import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select

from ..models import Chat, ChatMessage
from ..schemas import ChatSend, ChatUpdate
from ..services.chat import stream_chat
from ..services.errors import Invalid, NotFound
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1", tags=["chats"])


@router.get("/chats")
async def list_chats(db: DB, q: str = "", agent: str | None = None, limit: int = 200):
    stmt = select(Chat).order_by(Chat.updated_at.desc()).limit(min(limit, 500))
    if agent:
        stmt = stmt.where(Chat.agent_id == agent)
    if q.strip():
        like = f"%{q.strip()}%"
        hits = select(ChatMessage.chat_id).where(ChatMessage.text.ilike(like))
        stmt = stmt.where(or_(Chat.title.ilike(like), Chat.id.in_(hits)))
    chats = (await db.scalars(stmt)).all()
    out = []
    for c in chats:
        last = await db.scalar(select(ChatMessage).where(ChatMessage.chat_id == c.id, ChatMessage.role != "system").order_by(ChatMessage.at.desc()).limit(1))
        out.append(ser.chat(c, last))
    return out


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: str, db: DB):
    c = await db.get(Chat, chat_id)
    if not c:
        raise NotFound("Chat nicht gefunden")
    msgs = (await db.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat_id).order_by(ChatMessage.at))).all()
    return {**ser.chat(c), "messages": [ser.chat_message(m) for m in msgs]}


@router.patch("/chats/{chat_id}")
async def rename(chat_id: str, body: ChatUpdate, db: DB):
    c = await db.get(Chat, chat_id)
    if not c:
        raise NotFound("Chat nicht gefunden")
    t = body.title.strip()
    if not t:
        raise Invalid("Titel fehlt")
    c.title = t[:120]
    return ser.chat(c)


@router.delete("/chats/{chat_id}")
async def delete(chat_id: str, db: DB):
    c = await db.get(Chat, chat_id)
    if not c:
        raise NotFound("Chat nicht gefunden")
    await db.delete(c)
    return {"ok": True}


@router.post("/chat")
async def send(body: ChatSend):
    """Sends a message and streams the answer as NDJSON (see services/chat.py)."""
    if not body.text.strip() and not body.skill:
        raise Invalid("Nachricht fehlt")
    gen = stream_chat(chat_id=body.chat_id, agent_id=body.agent_id, text=body.text, model=body.model,
                      account_id=body.account_id, skill_id=body.skill, effort=body.effort)
    first = await gen.__anext__()  # validation errors surface as normal HTTP errors

    async def lines():
        yield json.dumps(first, ensure_ascii=False) + "\n"
        try:
            async for ev in gen:
                yield json.dumps(ev, ensure_ascii=False, default=str) + "\n"
        except (Invalid, NotFound) as e:
            yield json.dumps({"done": {"ok": False, "error": str(e)}}, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})
