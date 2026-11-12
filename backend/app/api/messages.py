from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import func, or_, select, update

from ..db import utcnow
from ..models import USER, Agent, Message
from ..services.errors import NotFound
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])
TASK_WAIT = timedelta(hours=1)  # no answer by then: the chat stops waiting for one


@router.get("")
async def list_messages(db: DB, box: str = "user", agent: str | None = None, limit: int = 100):
    """box=user: notifications and answers for you · box=all: everything (also between agents)."""
    stmt = select(Message).order_by(Message.at.desc()).limit(min(limit, 500))
    if box == "user":
        stmt = stmt.where(or_(Message.to_id == USER, Message.from_id == USER))
    if agent:
        stmt = stmt.where(or_(Message.to_id == agent, Message.from_id == agent))
    return [ser.message(m) for m in (await db.scalars(stmt)).all()]


@router.get("/inbox")
async def inbox(db: DB, limit: int = 60, before: str | None = None):
    """For the messages app: only what was sent to you, newest first, with the sender's name and the unread count."""
    stmt = select(Message).where(Message.to_id == USER).order_by(Message.at.desc()).limit(min(limit, 200))
    if before:
        ref = await db.get(Message, before)
        if ref:
            stmt = stmt.where(Message.at < ref.at)
    rows = (await db.scalars(stmt)).all()
    names = {a.id: a.name for a in (await db.scalars(select(Agent))).all()}
    unread = await db.scalar(select(func.count()).select_from(Message).where(Message.to_id == USER, Message.read_at.is_(None)))
    return {"unread": unread or 0, "items": [{**ser.message(m), "from_name": names.get(m.from_id, "IDA" if m.from_id == USER else m.from_id)} for m in rows]}


@router.get("/tasks")
async def tasks(db: DB, ids: str = ""):
    """How delegated tasks are doing, for the cards in the chat: waiting · working · done (with the answer) · silent (none came)."""
    wanted = [i for i in ids.split(",") if i][:50]
    rows = (await db.scalars(select(Message).where(Message.id.in_(wanted)))).all()
    answers = {r.reply_to: r for r in (await db.scalars(select(Message).where(Message.reply_to.in_(wanted)).order_by(Message.at))).all()}
    old = set((await db.scalars(select(Message.id).where(Message.id.in_(wanted), Message.at < utcnow() - TASK_WAIT))).all())
    out = []
    for m in rows:
        a = answers.get(m.id)
        status = "done" if a else "silent" if m.id in old else "working" if m.read_at else "waiting"
        out.append({"id": m.id, "status": status, "answer": a.text if a else None, "at": ser.iso(a.at) if a else None})
    return out


@router.post("/read-all")
async def read_all(db: DB):
    await db.execute(update(Message).where(Message.to_id == USER, Message.read_at.is_(None)).values(read_at=utcnow()))
    return {"ok": True}


@router.post("/{message_id}/read")
async def read(message_id: str, db: DB):
    m = await db.get(Message, message_id)
    if not m:
        raise NotFound("Nachricht nicht gefunden")
    m.read_at = m.read_at or utcnow()
    return ser.message(m)
