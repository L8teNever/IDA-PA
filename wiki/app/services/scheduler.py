"""Hintergrundpflege: Ablauf von Freigabe-Proposals & befristeten Memory-Seiten, 30-Tage-Papierkorb, tägliches Backup."""
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..auth import Caller
from ..config import get_settings
from ..db import pending, session_scope, utcnow
from ..models import Page, Proposal
from . import backup, files as file_svc, idem, store

log = logging.getLogger("wiki.scheduler")
_task: asyncio.Task | None = None
_last_backup_day: str | None = None


async def maintenance(now: datetime | None = None) -> dict:
    s = get_settings()
    at = now or utcnow()
    expired_props = 0
    expired_pages = 0
    purged_trash = 0

    async with session_scope(write=True) as db:
        # 1. Abgelaufene Proposals schließen
        props = (await db.scalars(
            select(Proposal).where(Proposal.status == "pending", Proposal.expires_at <= at)
        )).all()
        for p in props:
            p.status = "expired"
            p.decided_at = at
            expired_props += 1
            pending(db).append({"type": "proposal", "proposal_id": p.id, "status": "expired", "path": p.target_path, "by": p.agent_id})

        # 2. Befristete Seiten in `memory/` automatisch in den Papierkorb verschieben
        exp_rows = (await db.scalars(
            select(Page).where(Page.deleted_at.is_(None), Page.expires_at.is_not(None), Page.expires_at <= at)
        )).all()
        expiry_caller = Caller("system:expiry", "user", via_override="sync")
        for pg in exp_rows:
            await store.delete_page(db, expiry_caller, pg.id, note="Ablaufdatum erreicht")
            expired_pages += 1

        # 3. Endgültige Papierkorb-Bereinigung nach `trash_days` (Standard: 30 Tage)
        cutoff = at - timedelta(days=s.trash_days)
        old_trash = (await db.scalars(
            select(Page).where(Page.deleted_at.is_not(None), Page.deleted_at <= cutoff)
        )).all()
        for pg in old_trash:
            file_svc.purge_files_for_page(pg.id)
            await db.delete(pg)
            purged_trash += 1

        # 4. Alte Idempotenz-Einträge bereinigen
        purged_idem = await idem.purge(db)

    return {
        "expired_proposals": expired_props,
        "expired_pages": expired_pages,
        "purged_trash": purged_trash,
        "purged_idem": purged_idem,
    }


async def _loop() -> None:
    global _last_backup_day
    while True:
        try:
            await asyncio.sleep(60)
            await maintenance()
            s = get_settings()
            local_now = datetime.now(ZoneInfo(s.timezone))
            day_key = local_now.strftime("%Y-%m-%d")
            if local_now.hour == 3 and local_now.minute >= 45 and _last_backup_day != day_key:
                await asyncio.to_thread(backup.run_backup)
                _last_backup_day = day_key
                log.info("Tägliche Wiki-Sicherung erstellt")
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("Fehler im Wiki-Hintergrunddienst")


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
