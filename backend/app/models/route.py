from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, utcnow
from ._types import new_id


class RouteFeedback(Base):
    """How the user rated a model choice of "Auto" – Auto learns from these (services/autoroute.py).
    Kept when the chat is deleted (only a short snippet of the question is stored)."""

    __tablename__ = "route_feedback"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(String(12), unique=True)  # the rated answer
    kind: Mapped[str] = mapped_column(String(16), index=True)  # code | writing | research | chat | general
    difficulty: Mapped[int] = mapped_column(Integer)  # what the rules said (1–3), before learning
    model: Mapped[str] = mapped_column(String(64))  # what Auto took
    rating: Mapped[str] = mapped_column(String(8))  # ok | weak (too weak) | strong (stronger than needed)
    better_model: Mapped[str | None] = mapped_column(String(64), default=None)
    snippet: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
