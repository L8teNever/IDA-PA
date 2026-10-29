"""Datenbank: eine SQLite-Datei (WAL), Sessions, Basisklassen.

SQLite erlaubt nur einen Schreiber zur Zeit: Schreibende Sessions laufen unter `write_lock`.
Ereignisse (Live-Updates) werden erst nach dem Commit verschickt.
"""
import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .config import get_settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def new_id() -> str:
    """12 Zeichen (base32, klein) – enthält nie „_“, damit „<id>_<zeit>“ eindeutig bleibt."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(12))


class UTCDateTime(TypeDecorator):
    """SQLite liefert naive Zeiten zurück – hier kommen sie immer als UTC-aware heraus."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Zeit ohne Zeitzone")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
write_lock = asyncio.Lock()


def engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(get_settings().db_url, connect_args={"timeout": 30})

        @event.listens_for(_engine.sync_engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def pending(db: AsyncSession) -> list[dict]:
    """Ereignisse, die nach dem Commit an die offenen Fenster gehen."""
    return db.info.setdefault("events", [])


@asynccontextmanager
async def session_scope(write: bool = False):
    """write=True: unter dem Schreib-Lock, danach Commit und erst dann die Live-Ereignisse."""
    from .services import bus  # spät, damit db.py keine Kreise bildet

    engine()
    assert _sessionmaker is not None
    if write:
        await write_lock.acquire()
    try:
        async with _sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
            events = session.info.get("events") or []
    finally:
        if write:
            write_lock.release()
    for e in events:
        bus.publish(e)


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None
