from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, utcnow


class VoiceCall(Base):
    """One live conversation with Stella: phone call (out/in) or the live button in the app (web).
    The voice container reports it; the transcript ends up as a chat of the voice agent."""

    __tablename__ = "voice_calls"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)  # given by the voice container
    direction: Mapped[str] = mapped_column(String(8))  # out | in | web
    number: Mapped[str] = mapped_column(String(32), default="")
    owner: Mapped[bool] = mapped_column(Boolean, default=False)  # the user himself on the line (= tools allowed)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | dialing | live | done | failed | rejected | missed
    purpose: Mapped[str] = mapped_column(Text, default="")  # brief of an outbound call
    outcome: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[str | None] = mapped_column(String(12), default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
