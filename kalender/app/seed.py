"""Erster Start: ein paar Kalender, damit man sofort loslegen kann."""
from sqlalchemy import func, select

from .db import session_scope
from .models import Calendar


async def seed() -> None:
    async with session_scope(write=True) as db:
        if await db.scalar(select(func.count()).select_from(Calendar)):
            return
        db.add_all([
            Calendar(name="Privat", color="blau", is_default=True, position=0, agent_access="write", default_alarms=[]),
            Calendar(name="Ida", color="lila", position=1, agent_access="write", default_alarms=[]),
            Calendar(name="Feiertage", color="graphit", kind="holidays", position=9, agent_access="read", default_alarms=[]),
        ])
