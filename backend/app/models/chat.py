from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, Timestamps, utcnow
from ._types import Json, new_id


class Chat(Timestamps, Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="Neuer Chat")
    model: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str | None] = mapped_column(String(16), default=None)
    # Claude Code session to resume; reset when the account changes (sessions belong to one login).
    session_id: Mapped[str | None] = mapped_column(String(64), default=None)
    session_account: Mapped[str | None] = mapped_column(String(16), default=None)

    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="chat", cascade="all, delete-orphan", order_by="ChatMessage.at", lazy="raise")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant | system
    text: Mapped[str] = mapped_column(Text, default="")
    # assistant: ordered parts [{"t":"text","text":…} | {"t":"tool","server":…,"name":…,"input":…,"out":…,"error":bool}]
    parts: Mapped[list] = mapped_column(Json, default=list)
    meta: Mapped[dict] = mapped_column(Json, default=dict)  # model, account, skill, error …
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    chat: Mapped[Chat] = relationship(back_populates="messages")
