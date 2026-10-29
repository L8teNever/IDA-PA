"""Hintergrund-Schleife: Erinnerungen und Warteschlange alle 15 Sekunden, nachts Pflege und Backup."""
import asyncio
import logging

from ..config import get_settings
from ..db import session_scope
from . import alarms, backup, outbox, store
from .timeutil import local_now

log = logging.getLogger("kalender.scheduler")
TICK = 15
_task: asyncio.Task | None = None
_maint_day: str = ""


async def maintenance() -> None:
    s = get_settings()
    async with session_scope(write=True) as db:
        n = await store.purge_trash(db, s.trash_days)
        m = await store.purge_changes(db, s.changes_days)
    await alarms.cleanup()
    await backup.run()
    log.info("Pflege: %d Termine aus dem Papierkorb, %d alte Verlaufseinträge entfernt", n, m)


async def _loop() -> None:
    global _maint_day
    while True:
        try:
            await alarms.tick()
            await outbox.flush()
            now = local_now()
            if now.hour >= 3 and (now.hour > 3 or now.minute >= 30) and _maint_day != now.date().isoformat():
                _maint_day = now.date().isoformat()
                await maintenance()
        except asyncio.CancelledError:
            raise
        except Exception:  # die Schleife darf nie sterben
            log.exception("Hintergrund-Durchlauf fehlgeschlagen")
        await asyncio.sleep(TICK)


def start() -> None:
    global _task, _maint_day
    if _task is None or _task.done():
        _maint_day = local_now().date().isoformat() if local_now().hour >= 3 else ""
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
