from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, Timestamps


class Account(Timestamps, Base):
    """A Claude subscription login. Each one has its own CLAUDE_CONFIG_DIR below data/claude/<id>."""

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # a1, a2, …
    name: Mapped[str] = mapped_column(String(40))
    # Long-lived token from `claude setup-token` (alternative to an uploaded .credentials.json).
    oauth_token: Mapped[str | None] = mapped_column(Text, default=None)
    position: Mapped[int] = mapped_column(default=0)
