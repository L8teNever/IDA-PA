from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, Timestamps, utcnow
from ._types import Json, new_id


class Routine(Timestamps, Base):
    """A task that REPEATS: every interval_min minutes, at fixed times (times + days), when a trigger fires or on demand.
    Things that happen only once are reminders (models/reminder.py)."""

    __tablename__ = "routines"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(60))
    icon: Mapped[str] = mapped_column(String(40), default="schedule")
    prompt: Mapped[str] = mapped_column(Text)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    model: Mapped[str | None] = mapped_column(String(64), default=None)  # None = agent's model
    account_id: Mapped[str | None] = mapped_column(String(16), default=None)  # None = agent's account
    interval_min: Mapped[int] = mapped_column(default=60)
    # fixed times of day ["07:00", "18:30"] on weekdays [0=Mon … 6=Sun]; None = interval or trigger
    times: Mapped[list | None] = mapped_column(Json, default=None)
    days: Mapped[list | None] = mapped_column(Json, default=None)
    # no schedule at all: only started on demand (agents via MCP routine_run_now, "Jetzt ausführen")
    on_demand: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # {"type":"mcp","connector","tool","args","every_sec","mode":"new|change|contains","contains",
    #  "state":{…},"last_check","last_error","last_note","last_fired","last_preview"}
    trigger: Mapped[dict | None] = mapped_column(Json, default=None)
    fallback: Mapped[bool | None] = mapped_column(Boolean, default=None)  # None = like the agent
    memory_enabled: Mapped[bool | None] = mapped_column(Boolean, default=None)  # None = like the agent
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class RoutineRun(Base):
    __tablename__ = "routine_runs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    routine_id: Mapped[str] = mapped_column(ForeignKey("routines.id", ondelete="CASCADE"), index=True)
    via: Mapped[str] = mapped_column(String(12), default="schedule")  # schedule | trigger | manual
    ok: Mapped[bool] = mapped_column(Boolean)
    text: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list] = mapped_column(Json, default=list)
    account: Mapped[str | None] = mapped_column(String(40), default=None)
    ms: Mapped[int] = mapped_column(default=0)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
