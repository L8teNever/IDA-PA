"""Small key/value store on top of the settings table."""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Setting


async def get(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = await session.get(Setting, key)
    return default if row is None else row.value


async def put(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
