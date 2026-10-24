"""Skills: create / update / delete and which agents may use them."""
import re

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, AgentSkill, Skill
from .errors import Invalid, NotFound

SLUG = re.compile(r"^[a-z0-9-]{1,32}$")
RESERVED = {"help", "hilfe", "clear", "new", "neu"}


def slugify(text: str) -> str:
    s = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:32] or "skill"


def _clean(f: dict, partial: bool) -> dict:
    v = {}
    if not partial or "name" in f:
        n = str(f.get("name") or "").strip()
        if not n or len(n) > 60:
            raise Invalid("Name: 1–60 Zeichen")
        v["name"] = n
    if not partial or "body" in f:
        b = str(f.get("body") or "").strip()
        if not b or len(b) > 8000:
            raise Invalid("Die Anleitung fehlt (höchstens 8000 Zeichen)")
        v["body"] = b
    if "description" in f:
        d = str(f.get("description") or "").strip()
        if len(d) > 160:
            raise Invalid("Beschreibung: höchstens 160 Zeichen")
        v["description"] = d
    if f.get("icon"):
        v["icon"] = str(f["icon"])[:40]
    if f.get("enabled") is not None:
        v["enabled"] = bool(f["enabled"])
    return v


async def _set_agents(db: AsyncSession, skill_id: str, agent_ids: list[str]) -> None:
    valid = set((await db.scalars(select(Agent.id).where(Agent.id.in_(agent_ids)))).all())
    await db.execute(delete(AgentSkill).where(AgentSkill.skill_id == skill_id))
    for a in agent_ids:
        if a in valid:
            db.add(AgentSkill(agent_id=a, skill_id=skill_id))


async def agents_of(db: AsyncSession, skill_id: str) -> list[str]:
    return list((await db.scalars(select(AgentSkill.agent_id).where(AgentSkill.skill_id == skill_id))).all())


async def create(db: AsyncSession, f: dict) -> Skill:
    v = _clean(f, False)
    sid = str(f.get("id") or "").strip().lower().lstrip("/") or slugify(v["name"])
    if not SLUG.match(sid) or sid in RESERVED:
        raise Invalid("Kürzel: a–z, 0–9 und - (so tippst du ihn nach dem /)")
    base, n = sid, 2
    while await db.get(Skill, sid):
        if f.get("id"):
            raise Invalid("Diesen Skill gibt es schon")
        sid, n = f"{base[:29]}-{n}", n + 1
    s = Skill(id=sid, **{"description": "", **v})
    db.add(s)
    await db.flush()
    await _set_agents(db, sid, list(f.get("agents") or []))
    return s


async def update(db: AsyncSession, skill_id: str, f: dict) -> Skill:
    s = await db.get(Skill, skill_id)
    if not s:
        raise NotFound("Skill nicht gefunden")
    for k, val in _clean(f, True).items():
        setattr(s, k, val)
    if f.get("agents") is not None:
        await _set_agents(db, skill_id, list(f["agents"]))
    return s


async def remove(db: AsyncSession, skill_id: str) -> None:
    s = await db.get(Skill, skill_id)
    if not s:
        raise NotFound("Skill nicht gefunden")
    await db.delete(s)
