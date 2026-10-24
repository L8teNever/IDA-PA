from fastapi import APIRouter
from sqlalchemy import select

from ..models import Skill
from ..schemas import SkillCreate, SkillUpdate
from ..services import skills as svc
from ..services.errors import NotFound
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


@router.get("")
async def list_skills(db: DB):
    return [ser.skill(s, await svc.agents_of(db, s.id)) for s in (await db.scalars(select(Skill).order_by(Skill.name))).all()]


@router.get("/{skill_id}")
async def get_skill(skill_id: str, db: DB):
    s = await db.get(Skill, skill_id)
    if not s:
        raise NotFound("Skill nicht gefunden")
    return ser.skill(s, await svc.agents_of(db, s.id))


@router.post("", status_code=201)
async def create(body: SkillCreate, db: DB):
    s = await svc.create(db, body.model_dump())
    return ser.skill(s, await svc.agents_of(db, s.id))


@router.patch("/{skill_id}")
async def update(skill_id: str, body: SkillUpdate, db: DB):
    s = await svc.update(db, skill_id, body.model_dump(exclude_unset=True))
    await db.flush()
    return ser.skill(s, await svc.agents_of(db, s.id))


@router.delete("/{skill_id}")
async def delete(skill_id: str, db: DB):
    await svc.remove(db, skill_id)
    return {"ok": True}
