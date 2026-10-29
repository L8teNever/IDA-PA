"""Warteschlange an IDA: fällige Erinnerungen werden per Webhook geschickt und bleiben liegen, bis IDA sie annimmt."""
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import Outbox

log = logging.getLogger("kalender.outbox")
GIVE_UP = timedelta(hours=1)


def _delay(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(0, attempts - 1), 15 * 60))


async def flush(now: datetime | None = None) -> int:
    """Schickt alle fälligen Meldungen. → Anzahl erfolgreich zugestellt."""
    s = get_settings()
    now = now or utcnow()
    async with session_scope() as db:
        rows = (await db.scalars(select(Outbox).where(Outbox.next_try <= now).order_by(Outbox.created_at).limit(20))).all()
        batch = [(r.id, r.kind, dict(r.payload), r.attempts, r.created_at) for r in rows]
    if not batch:
        return 0
    if not s.ida_webhook_url:
        async with session_scope(write=True) as db:
            await db.execute(delete(Outbox).where(Outbox.id.in_([b[0] for b in batch])))
        return 0
    done, retry = [], {}
    headers = {"Authorization": f"Bearer {s.kalender_ida_token}"} if s.kalender_ida_token else {}
    async with httpx.AsyncClient(timeout=10) as client:
        for oid, kind, payload, attempts, created in batch:
            try:
                r = await client.post(s.ida_webhook_url, json={**payload, "kind": kind}, headers=headers)
                if r.status_code < 300:
                    done.append(oid)
                    continue
                err = f"HTTP {r.status_code}"
            except httpx.HTTPError as e:
                err = e.__class__.__name__
            if now - created > GIVE_UP:
                log.warning("Meldung %s an IDA verworfen (%s, älter als 1 Stunde)", oid, err)
                done.append(oid)
            else:
                retry[oid] = (attempts + 1, err)
    async with session_scope(write=True) as db:
        if done:
            await db.execute(delete(Outbox).where(Outbox.id.in_(done)))
        for oid, (n, err) in retry.items():
            row = await db.get(Outbox, oid)
            if row:
                row.attempts, row.last_error, row.next_try = n, err, now + _delay(n)
    return len([d for d in done if d not in retry])
