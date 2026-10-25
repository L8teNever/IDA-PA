from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, Timestamps
from ._types import new_id


class Reminder(Timestamps, Base):
    """Something that happens exactly ONCE at a point in time („erinnere mich morgen um 9 an …“, „mach das am Freitag“).

    Not a routine: routines repeat (interval, times, trigger, on demand), reminders fire once and are then done.
      kind "remind": no AI – the text simply arrives as a notification for the user.
      kind "task":   the agent carries out the text at that moment; the result arrives as a notification.
    """

    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(8), default="remind")  # remind | task
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(8), default="pending")  # pending | running | done | failed
    result: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(32), default="user")  # "user" or the agent that set it up
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
