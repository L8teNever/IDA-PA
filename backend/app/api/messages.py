from fastapi import APIRouter
from sqlalchemy import or_, select, update

from ..db import utcnow
from ..models import USER, Message
from ..services.errors import NotFound
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])


@router.get("")
async def list_messages(db: DB, box: str = "user", agent: str | None = None, limit: int = 100):
    """box=user: notifications and answers for you · box=all: everything (also between agents)."""
    stmt = select(Message).order_by(Message.at.desc()).limit(min(limit, 500))
    if box == "user":
        stmt = stmt.where(or_(Message.to_id == USER, Message.from_id == USER))
    if agent:
        stmt = stmt.where(or_(Message.to_id == agent, Message.from_id == agent))
    return [ser.message(m) for m in (await db.scalars(stmt)).all()]


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
