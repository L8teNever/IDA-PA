from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, utcnow
from ._types import new_id


class PushSubscription(Base):
    """A device (browser/PWA) that gets your messages as push notifications (Web Push, messages.kulbarts.com)."""

    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(64))
    device: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_ok: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
