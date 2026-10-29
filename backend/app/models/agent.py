from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, Timestamps, utcnow
from ._types import Json, new_id


class Agent(Timestamps, Base):
    """The main agent (is_main, the user's assistant) and all sub-agents."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # slug, also used in URLs and prompts
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    name: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(120), default="")  # short subtitle in menus
    intro: Mapped[str] = mapped_column(String(300), default="")  # shown on the agent's start page
    palette: Mapped[str] = mapped_column(String(16), default="blau")  # colour scheme (frontend palettes)
    home: Mapped[str] = mapped_column(String(16), default="simple")  # "timeline" | "simple"
    chips: Mapped[list] = mapped_column(Json, default=list)  # quick starts: [[icon, text], …]
    position: Mapped[int] = mapped_column(default=0)
    avatar: Mapped[str | None] = mapped_column(String(16), default=None)  # version of the profile picture file (None = none)

    # prompt parts
    role: Mapped[str] = mapped_column(Text, default="")
    personality: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")  # scratchpad the agent keeps between runs

    model: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), default=None)
    fallback: Mapped[bool] = mapped_column(Boolean, default=True)  # other account when the limit is hit
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    connectors: Mapped[list["AgentConnector"]] = relationship(back_populates="agent", cascade="all, delete-orphan", lazy="selectin")
    runs: Mapped[list["AgentRun"]] = relationship(back_populates="agent", cascade="all, delete-orphan", order_by="desc(AgentRun.at)", lazy="raise")


class AgentConnector(Base):
    """Which connector an agent may use, optionally limited to some tools (None = all enabled tools)."""

    __tablename__ = "agent_connectors"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"), primary_key=True)
    tools: Mapped[list | None] = mapped_column(Json, default=None)

    agent: Mapped[Agent] = relationship(back_populates="connectors")


class AgentRun(Base):
    """One background run of an agent (message, routine, delegated task)."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(80))
    ok: Mapped[bool] = mapped_column(Boolean)
    text: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list] = mapped_column(Json, default=list)
    account: Mapped[str | None] = mapped_column(String(40), default=None)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    agent: Mapped[Agent] = relationship(back_populates="runs")
