"""Datenbank: eine SQLite-Datei (WAL). Schreibende Sessions laufen unter `write_lock` (SQLite hat einen Schreiber),
Live-Ereignisse gehen erst nach dem Commit raus."""
import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from .config import get_settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
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
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


@asynccontextmanager
async def session_scope(write: bool = False):
    from . import bus  # spät, damit db.py keine Kreise bildet

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


def pending(db: AsyncSession) -> list[dict]:
    return db.info.setdefault("events", [])


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None
