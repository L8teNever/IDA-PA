from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from ..models import USER, Agent, AgentConnector, AgentRun, AgentSkill, Message
from ..schemas import Access, AgentCreate, AgentUpdate, Notes, UserMessage
from ..services import agents as svc
from ..services import files
from ..services.errors import NotFound
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


async def _detail(db, a) -> dict:
    access = (await db.scalars(select(AgentConnector).where(AgentConnector.agent_id == a.id))).all()
    skills = (await db.scalars(select(AgentSkill.skill_id).where(AgentSkill.agent_id == a.id))).all()
    runs = (await db.scalars(select(AgentRun).where(AgentRun.agent_id == a.id).order_by(AgentRun.at.desc()).limit(10))).all()
    unread = await db.scalar(select(func.count()).select_from(Message).where(Message.to_id == a.id, Message.read_at.is_(None)))
    return ser.agent(a, {"connectors": {x.connector_id: x.tools for x in access}, "skills": list(skills),
                         "runs": [ser.agent_run(r) for r in runs], "unread": unread or 0})


@router.get("")
async def list_agents(db: DB):
    return [ser.agent(a) for a in (await db.scalars(select(Agent).order_by(Agent.position))).all()]


@router.get("/{agent_id}")
async def get_agent(agent_id: str, db: DB):
    return await _detail(db, await svc.get(db, agent_id))


@router.post("", status_code=201)
async def create(body: AgentCreate, db: DB):
    a = await svc.create(db, body.model_dump())
    return await _detail(db, a)


@router.patch("/{agent_id}")
async def update(agent_id: str, body: AgentUpdate, db: DB):
    a = await svc.update_agent(db, agent_id, body.model_dump(exclude_unset=True))
    return await _detail(db, a)


@router.delete("/{agent_id}")
async def delete(agent_id: str, db: DB):
    await svc.remove(db, agent_id)
    return {"ok": True}


@router.get("/{agent_id}/avatar")
async def get_avatar(agent_id: str, db: DB):
    a = await svc.get(db, agent_id)
    p = svc.avatar_path(agent_id)
    if not a.avatar or not p.exists():
        raise NotFound("Kein Profilbild")
    _, mime = files.kind_of("avatar", "", p.read_bytes()[:4096])
    return FileResponse(p, media_type=mime, headers={"cache-control": "private, max-age=31536000, immutable"})


@router.post("/{agent_id}/avatar")
async def set_avatar(agent_id: str, file: UploadFile, db: DB):
    """Profile picture of the agent (one image; the app shrinks it to a square before uploading)."""
    data = await file.read(svc.MAX_AVATAR + 1)
    return await _detail(db, await svc.set_avatar(db, agent_id, file.filename or "avatar", data))


@router.delete("/{agent_id}/avatar")
async def clear_avatar(agent_id: str, db: DB):
    return await _detail(db, await svc.clear_avatar(db, agent_id))


@router.put("/{agent_id}/notes")
async def notes(agent_id: str, body: Notes, db: DB):
    return {"notes": await svc.set_notes(db, agent_id, body.text)}


@router.put("/{agent_id}/connectors/{connector_id}")
async def access(agent_id: str, connector_id: str, body: Access, db: DB):
    await svc.set_access(db, agent_id, connector_id, body.allowed, body.tools)
    return {"ok": True}


@router.post("/{agent_id}/message")
async def message(agent_id: str, body: UserMessage, db: DB):
    """The user writes to an agent's inbox (wakes it up; the answer comes back as a message)."""
    m = await svc.send(db, from_id=USER, to=agent_id, text=body.text, priority=body.priority, expect_reply=True, depth=1)
    return ser.message(m)
