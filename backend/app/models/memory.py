from datetime import datetime

from sqlalchemy import DateTime, Index, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, utcnow
from ._types import Json, new_id


class MemoryEntry(Base):
    """One fact in a memory scope: "shared", or an agent id (that agent's own memory)."""

    __tablename__ = "memory_entries"
    __table_args__ = (Index("ix_memory_entries_scope_pinned", "scope", "pinned"),)

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(12), default="fact")  # fact | preference | rule | event
    pinned: Mapped[bool] = mapped_column(default=False)
    importance: Mapped[int] = mapped_column(default=3)
    by: Mapped[str] = mapped_column(String(32), default="user")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    uses: Mapped[int] = mapped_column(default=0)
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # float32 vector of the embedding model (Ollama), None until computed
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    embed_model: Mapped[str | None] = mapped_column(String(80), default=None)


class MemoryBackup(Base):
    """State of a scope before the last consolidation (one level of undo)."""

    __tablename__ = "memory_backups"

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    data: Mapped[list] = mapped_column(Json)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

