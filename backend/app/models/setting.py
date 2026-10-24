from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._types import Json


class Setting(Base):
    """Key/value settings (default model, password hash, secret key, …)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(Json, default=None)
    note: Mapped[str] = mapped_column(Text, default="")
