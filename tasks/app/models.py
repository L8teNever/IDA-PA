"""Eine Aufgabe.

* `day`  – ab diesem Tag steht sie auf der Startseite („mach ich am …“). Leer = sofort. Liegt er in der Zukunft, steht sie unter „Geplant“.
* `due`  – optional: bis wann („in dem Zeitraum bis …“). Nur Anzeige und Sortierung, sie verschwindet danach nicht.
* `priority` – 0 = keine, 1 = niedrig, 2 = mittel, 3 = hoch.
* `done_at` – erledigt (Liste „Erledigt“, dort wiederherstellbar).
"""
from datetime import date, datetime

from sqlalchemy import Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime, new_id, utcnow


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    day: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    due: Mapped[date | None] = mapped_column(Date, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(40), default="user")
    updated_by: Mapped[str] = mapped_column(String(40), default="user")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
