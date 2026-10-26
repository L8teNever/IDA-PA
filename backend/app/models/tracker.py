from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, Timestamps, utcnow
from ._types import Json, new_id


class Tracker(Timestamps, Base):
    """Something to tick off every day („Tablette morgens + abends“) – by the user or an agent.

    slots: [{"key", "label", "time"}] – one tick per slot and day; with a time, IDA follows up when it is still open:
      on_missed "notify":  a notification (no AI)
      on_missed "routine": routine_id is started with a note which slot is open (at most once per slot and day)
    """

    __tablename__ = "trackers"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(60))
    icon: Mapped[str] = mapped_column(String(40), default="task_alt")
    note: Mapped[str] = mapped_column(Text, default="")
    slots: Mapped[list] = mapped_column(Json, default=list)
    days: Mapped[list | None] = mapped_column(Json, default=None)  # 0=Mon … 6=Sun; None/empty = every day
    on_missed: Mapped[str] = mapped_column(String(8), default="none")  # none | notify | routine
    # > 0: ticking via link / timer page first runs a countdown of this many seconds (e.g. 180 = brush teeth 3 min)
    duration_s: Mapped[int] = mapped_column(default=0, server_default="0")
    routine_id: Mapped[str | None] = mapped_column(ForeignKey("routines.id", ondelete="SET NULL"), default=None, index=True)
    fired: Mapped[dict] = mapped_column(Json, default=dict)  # {slot_key: "YYYY-MM-DD"} – followed up on that day already
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    show_on_home: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")  # in the start page's day plan
    created_by: Mapped[str] = mapped_column(String(32), default="user")  # "user" or the agent that set it up
    position: Mapped[int] = mapped_column(default=0)


class TrackerCheck(Base):
    """One tick: slot `slot` of tracker `tracker_id` done on local day `day` (YYYY-MM-DD)."""

    __tablename__ = "tracker_checks"
    __table_args__ = (UniqueConstraint("tracker_id", "day", "slot"),)

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    tracker_id: Mapped[str] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    slot: Mapped[str] = mapped_column(String(12))
    by: Mapped[str] = mapped_column(String(32), default="user")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrackerLink(Base):
    """A secret URL /t/<token> (e.g. on an NFC tag): opening it ticks the tracker – the slot that fits the time, or `slot`."""

    __tablename__ = "tracker_links"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    tracker_id: Mapped[str] = mapped_column(ForeignKey("trackers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40), default="NFC-Tag")
    token: Mapped[str] = mapped_column(String(32), unique=True)
    slot: Mapped[str | None] = mapped_column(String(12), default=None)  # None = automatically by the time of day
    uses: Mapped[int] = mapped_column(default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
