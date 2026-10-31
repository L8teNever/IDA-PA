"""SQLAlchemy-Modelle des IDA Wiki (siehe alembic/versions/0001_init.py)."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, Timestamps, UTCDateTime, new_id, utcnow

SPACES = ("privat", "memory", "ki")


class Page(Timestamps, Base):
    __tablename__ = "pages"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    space: Mapped[str] = mapped_column(String(16), index=True)
    path: Mapped[str] = mapped_column(String(320), unique=True)        # z. B. "privat/01-alltag-wohnen/mietvertrag" (ohne .md)
    folder: Mapped[str] = mapped_column(String(280), default="", index=True)
    slug: Mapped[str] = mapped_column(String(140), index=True)         # Dateiname ohne .md, kleingeschrieben für [[slug]]
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(300), default="")
    frontmatter: Mapped[dict] = mapped_column(JSON, default=dict)
    body_hash: Mapped[str] = mapped_column(String(64), default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    importance: Mapped[int] = mapped_column(Integer, default=3)        # 1..5 (für Memory-Kontext)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    managed_by: Mapped[str | None] = mapped_column(String(24), nullable=True)  # "ida_sync" für ki/
    current_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="user")
    updated_by: Mapped[str] = mapped_column(String(64), default="user")
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embed_model: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PageLink(Base):
    __tablename__ = "page_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_page_id: Mapped[str] = mapped_column(String(12), ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    target_page_id: Mapped[str | None] = mapped_column(String(12), ForeignKey("pages.id", ondelete="SET NULL"), nullable=True, index=True)
    target_raw: Mapped[str] = mapped_column(String(320))
    heading: Mapped[str | None] = mapped_column(String(160), nullable=True)
    alias: Mapped[str | None] = mapped_column(String(160), nullable=True)
    is_embed: Mapped[bool] = mapped_column(Boolean, default=False)
    context_snippet: Mapped[str] = mapped_column(String(280), default="")


class PageTag(Base):
    __tablename__ = "page_tags"

    page_id: Mapped[str] = mapped_column(String(12), ForeignKey("pages.id", ondelete="CASCADE"), primary_key=True)
    tag: Mapped[str] = mapped_column(String(80), primary_key=True, index=True)


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(String(12), ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))                      # image | pdf | text | audio | video | archive | other
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    source_chat_file_id: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    uploaded_by: Mapped[str] = mapped_column(String(64), default="user")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Revision(Base):
    __tablename__ = "revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_id: Mapped[str] = mapped_column(String(12), ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    rev_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))                    # create | update | move | delete | restore | attach | detach
    space: Mapped[str] = mapped_column(String(16), index=True)
    path_snapshot: Mapped[str] = mapped_column(String(320))
    title_snapshot: Mapped[str] = mapped_column(String(200))
    content_full: Mapped[str] = mapped_column(Text)
    added_lines: Mapped[int] = mapped_column(Integer, default=0)
    deleted_lines: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(240), default="")
    by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    via: Mapped[str] = mapped_column(String(16))                       # ui | ida | mcp | sync
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(24))                    # create | patch | update | move | delete | attach
    target_path: Mapped[str] = mapped_column(String(320))
    summary: Mapped[str] = mapped_column(String(240), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    diff_preview: Mapped[str] = mapped_column(Text, default="")
    commit_secret_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class McpToken(Base):
    __tablename__ = "mcp_tokens"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(60))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(12))
    allow_read_private: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_read_chats: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RequestRec(Base):
    __tablename__ = "requests"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    status: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
