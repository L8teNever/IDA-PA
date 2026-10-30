"""Tabellen des Kalenders (eigene SQLite-Datei, siehe docs/KALENDER.md §3)."""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, Timestamps, UTCDateTime, new_id, utcnow


class Calendar(Timestamps, Base):
    __tablename__ = "calendars"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80))
    color: Mapped[str] = mapped_column(String(16), default="blau")
    kind: Mapped[str] = mapped_column(String(12), default="normal")        # normal | birthdays | holidays
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    default_alarms: Mapped[list] = mapped_column(JSON, default=list)       # Minuten vorher, z. B. [10]
    agent_access: Mapped[str] = mapped_column(String(8), default="write")  # write | read | none
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class Event(Timestamps, Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    calendar_id: Mapped[str] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"))
    uid: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    start_at: Mapped[datetime] = mapped_column(UTCDateTime)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime)                  # bei ganztägig exklusiv
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    tzid: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")
    rrule: Mapped[str] = mapped_column(String(500), default="")
    exdates: Mapped[list] = mapped_column(JSON, default=list)              # ["20261029T1500", …] in Ortszeit des Termins
    series_end: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # Ende des letzten Vorkommens; NULL = endlos
    recurrence_parent_id: Mapped[str | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=True)
    recurrence_original_start: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="confirmed")   # confirmed | tentative | cancelled
    transparency: Mapped[str] = mapped_column(String(8), default="busy")   # busy | free
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alarms: Mapped[list] = mapped_column(JSON, default=list)
    alarm_action: Mapped[str] = mapped_column(String(8), default="notify") # notify | agent
    alarm_task: Mapped[str] = mapped_column(String(500), default="")       # Aufgabe für den Agenten bei alarm_action=agent
    travel_min: Mapped[int] = mapped_column(Integer, default=0)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attendees: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(40), default="user")
    updated_by: Mapped[str] = mapped_column(String(40), default="user")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        Index("ix_events_calendar_start", "calendar_id", "start_at"),
        Index("ix_events_end_at", "end_at"),
        Index("ix_events_series_end", "series_end"),
        Index("ix_events_parent", "recurrence_parent_id"),
        Index("ix_events_deleted_at", "deleted_at"),
    )


class AlarmSent(Base):
    __tablename__ = "alarms_sent"
    event_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    occurrence_start: Mapped[str] = mapped_column(String(16), primary_key=True)
    minutes: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Change(Base):
    """Verlauf, Live-Bus und Rückgängig: eine Zeile je geändertem Termin."""
    __tablename__ = "changes"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    batch: Mapped[str] = mapped_column(String(12))                         # gehört zusammen (z. B. „dieser und folgende“)
    event_id: Mapped[str] = mapped_column(String(12))
    calendar_id: Mapped[str] = mapped_column(String(12), default="")
    action: Mapped[str] = mapped_column(String(10))                        # create | update | delete | restore
    by: Mapped[str] = mapped_column(String(40), default="user")
    via: Mapped[str] = mapped_column(String(10), default="ui")             # ui | ida | mcp | ics | api
    title: Mapped[str] = mapped_column(String(300), default="")
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (Index("ix_changes_at", "at"), Index("ix_changes_by", "by"), Index("ix_changes_batch", "batch"))


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSON, nullable=True)


class Outbox(Base):
    """Wartende Meldungen an IDA (Webhook); bleiben liegen, bis IDA sie annimmt."""
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_try: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (Index("ix_outbox_next_try", "next_try"),)


class RequestRec(Base):
    """Bereits ausgeführte Schreibaufrufe mit Idempotency-Key: dieselbe Wiederholung bekommt dieselbe Antwort, die Änderung geschieht nur einmal."""
    __tablename__ = "requests"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    status: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (Index("ix_requests_at", "at"),)
