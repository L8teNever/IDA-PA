"""Background loop: due routines, trigger checks and memory upkeep."""
import asyncio
import logging
import time

from ..db import session_scope
from . import routines
from .memory import embeddings
from .memory import store as memory

log = logging.getLogger("ida.scheduler")
TICK = 5  # seconds
_task: asyncio.Task | None = None


async def _loop() -> None:
    last_upkeep = 0.0
    while True:
        try:
            await routines.schedule_tick()
            if time.monotonic() - last_upkeep > 300:
                last_upkeep = time.monotonic()
                async with session_scope() as db:
                    await memory.purge(db)
                await embeddings.backfill()
        except asyncio.CancelledError:
            raise
        except Exception:  # keep the loop alive whatever happens
            log.exception("scheduler tick failed")
        await asyncio.sleep(TICK)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
