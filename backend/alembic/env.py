"""Alembic environment: runs migrations with the app's async engine (PostgreSQL or SQLite)."""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401  (registers all tables)
from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _configure(connection=None, url=None):
    is_sqlite = (url or str(connection.engine.url)).startswith("sqlite")
    context.configure(
        connection=connection, url=url, target_metadata=target_metadata,
        render_as_batch=is_sqlite,  # SQLite needs table rebuilds for ALTER
        compare_type=True,
    )


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
