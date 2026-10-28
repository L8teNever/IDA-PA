"""Code area: projects (git repos), GitHub sign-in, git actions and approvals for code threads.

Switched off by default (Einstellungen → Code): code threads run Claude with real file and shell tools
in the project folder, and IDA has no login of its own – only switch it on behind an access protection.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings_store
from ..errors import Invalid

ENABLED_KEY = "code_enabled"


async def enabled(db: AsyncSession) -> bool:
    return bool(await settings_store.get(db, ENABLED_KEY, False))


async def require(db: AsyncSession) -> None:
    if not await enabled(db):
        raise Invalid("Der Code-Bereich ist ausgeschaltet (Einstellungen → Code)")
