"""Health check and the data every page needs at start (agents, models, accounts, counters)."""
from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from ..config import get_settings
from ..db import utcnow
from ..models import USER, Account, Agent, Message, Routine, RoutineRun, Skill
from ..services import activity, jobs
from ..services.agents import PALETTES
from ..services.claude.usage import logged_in
from . import serializers as ser
from .deps import DB

router = APIRouter(tags=["system"])


@router.get("/api/v1/health")
async def health():
    return {"ok": True}


@router.get("/api/v1/activity")
async def current_activity():
    """What the agents are doing right now (polled by the browser for the mascot)."""
    return {**activity.snapshot(), "jobs": jobs.status()}


@router.get("/api/v1/bootstrap")
async def bootstrap(db: DB):
    s = get_settings()
    agents = (await db.scalars(select(Agent).order_by(Agent.position))).all()
    accounts = (await db.scalars(select(Account).order_by(Account.position, Account.id))).all()
    skills = (await db.scalars(select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.name))).all()
    unread_user = await db.scalar(select(func.count()).select_from(Message).where(Message.to_id == USER, Message.read_at.is_(None)))
    return {
        "models": s.model_list, "default_model": s.default_model, "palettes": list(PALETTES),
        "agents": [ser.agent(a) for a in agents],
        "accounts": [ser.account(a, logged_in(a)) for a in accounts],
        "skills": [{"id": k.id, "name": k.name, "icon": k.icon, "description": k.description} for k in skills],
        "unread": unread_user or 0, "jobs": jobs.status(),
    }


@router.get("/api/v1/home")
async def home(db: DB):
    """Start page: unread notifications (Infos) and today's timeline (routine runs + upcoming runs)."""
    now = utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    notices = (await db.scalars(select(Message).where(Message.to_id == USER, Message.read_at.is_(None)).order_by(Message.priority.desc(), Message.at.desc()).limit(20))).all()
    routines = {r.id: r for r in (await db.scalars(select(Routine))).all()}
    runs = (await db.scalars(select(RoutineRun).where(RoutineRun.at >= start).order_by(RoutineRun.at))).all()
    timeline = [{"kind": "run", "at": ser.iso(x.at), "title": routines[x.routine_id].name if x.routine_id in routines else "Routine",
                 "icon": routines[x.routine_id].icon if x.routine_id in routines else "schedule", "ok": x.ok,
                 "agent_id": routines[x.routine_id].agent_id if x.routine_id in routines else None, "routine_id": x.routine_id,
                 "text": x.text[:300]} for x in runs]
    end = start + timedelta(days=1)
    for r in routines.values():
        nxt = r.next_run.replace(tzinfo=now.tzinfo) if r.next_run and r.next_run.tzinfo is None else r.next_run
        if r.enabled and nxt and nxt < end:
            timeline.append({"kind": "next", "at": ser.iso(nxt), "title": r.name, "icon": r.icon, "agent_id": r.agent_id, "routine_id": r.id})
    timeline.sort(key=lambda x: x["at"])
    return {"notices": [ser.message(m) for m in notices], "timeline": timeline}
