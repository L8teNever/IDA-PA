from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session

# scope="function": commit BEFORE the response is sent, so the next request already sees the change
DB = Annotated[AsyncSession, Depends(get_session, scope="function")]
