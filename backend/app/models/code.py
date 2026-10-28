from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, Timestamps
from ._types import new_id


class CodeProject(Timestamps, Base):
    """A code project: a git repository in data/projects/<id> (cloned from GitHub or started empty).
    Its threads are chats with kind="code" and project_id set."""

    __tablename__ = "code_projects"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80))
    repo_url: Mapped[str | None] = mapped_column(String(300), default=None)  # clone URL (https), None = local only
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    status: Mapped[str] = mapped_column(String(10), default="ready")  # cloning | ready | error
    status_text: Mapped[str] = mapped_column(Text, default="")
