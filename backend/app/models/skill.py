from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, Timestamps


class Skill(Timestamps, Base):
    """Reusable instructions. Agents see name + description and load the body when needed; /slug forces it."""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # slug, typed after "/"
    name: Mapped[str] = mapped_column(String(60))
    icon: Mapped[str] = mapped_column(String(40), default="auto_fix_high")
    description: Mapped[str] = mapped_column(String(160), default="")
    body: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentSkill(Base):
    """Agents that may use a skill."""

    __tablename__ = "agent_skills"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)
