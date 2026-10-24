from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, utcnow
from ._types import new_id

USER = "user"  # the human; notifications are messages to "user"


class Message(Base):
    """Agent ↔ agent messages, delegated tasks, results and notifications for the user."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_to_read", "to_id", "read_at"),)

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    from_id: Mapped[str] = mapped_column(String(32), index=True)
    to_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(12), default="message")  # message | task | result | notice
    priority: Mapped[str] = mapped_column(String(8), default="normal")  # normal | high
    title: Mapped[str] = mapped_column(String(120), default="")
    text: Mapped[str] = mapped_column(Text)
    expect_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    depth: Mapped[int] = mapped_column(default=0)  # length of the chain of messages triggering each other
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
