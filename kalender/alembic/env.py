"""Alembic für den Kalender: eine SQLite-Datei, asynchron."""
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401
from app.config import get_settings
from app.db import Base

config = context.config
target_metadata = Base.metadata


def _configure(connection=None, url=None):
    context.configure(connection=connection, url=url, target_metadata=target_metadata, render_as_batch=True, compare_type=True)


def run_offline():
    _configure(url=get_settings().db_url)
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection):
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = create_async_engine(get_settings().db_url)
    async with engine.connect() as conn:
        await conn.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
