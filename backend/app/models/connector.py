from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, Timestamps
from ._types import Json


class Connector(Timestamps, Base):
    """A remote MCP server (streamable HTTP)."""

    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # a-z 0-9 _ -
    name: Mapped[str] = mapped_column(String(60))
    url: Mapped[str] = mapped_column(Text)  # may contain a token (?token=…) – never sent to the browser unmasked
    headers: Mapped[dict] = mapped_column(Json, default=dict)
    # OAuth (services/mcp/oauth.py): grant, client_id, client_secret, scope, meta, access_token, refresh_token, expires_at, pending
    oauth: Mapped[dict] = mapped_column(Json, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tools: Mapped[list] = mapped_column(Json, default=list)  # all tool names of the server
    disabled_tools: Mapped[list] = mapped_column(Json, default=list)  # switched off for everybody
    # Was der Dienst ist und wie der Nutzer ihn nennt ("Sofia = meine Schul-App ...") – steht im Kontext jedes Agenten mit Zugriff.
    context: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | ok | error
    status_text: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    @property
    def enabled_tools(self) -> list[str]:
        return [t for t in self.tools if t not in self.disabled_tools]
