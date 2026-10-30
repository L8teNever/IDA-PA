"""Abgleich für die App auf dem Handy: nur das, was sich seit dem letzten Mal geändert hat (docs/KALENDER-OFFLINE-AUFGABEN.md, Paket 6)."""
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from ..auth import Caller, caller
from ..db import session_scope
from ..errors import Invalid
from ..models import Calendar, Change, Event, Setting
from ..services import query, store
from ..services import recurrence as rec
from ..services import settings as cfg
from ..services.timeutil import add_months, local_now, parse_input
from . import calendars as cal_api

router = APIRouter(prefix="/api/v1", tags=["sync"])
MAX_MONTHS = 25
MAX_CHANGED = 2000


@router.get("/sync")
async def sync(since: int | None = None, frm: str | None = Query(None, alias="from"), to: str | None = None, meta_at: str | None = None,
               c: Caller = Depends(caller)):
    """Delta seit `since` (Cursor aus der letzten Antwort). Ohne `since`, bei zu altem oder unbekanntem Cursor oder sehr vielen Änderungen
    kommt der volle Stand des Zeitraums (`full: true`). Die App ersetzt bei jedem genannten Termin ALLE seine Vorkommen."""
    today = local_now().date()
    a, _ = parse_input(frm or add_months(today.replace(day=1), -3).isoformat())   # Zeitraum [von, bis): bis ist der erste Tag NACH dem Zeitraum
    b, _ = parse_input(to or add_months(today.replace(day=1), 12).isoformat())
    if b <= a:
        raise Invalid("Das Ende liegt nicht nach dem Anfang")
    if b - a > timedelta(days=MAX_MONTHS * 31):
        raise Invalid(f"Zeitraum zu groß (höchstens {MAX_MONTHS} Monate)")
    async with session_scope() as db:
        cursor = await db.scalar(select(func.max(Change.id))) or 0
        oldest = await db.scalar(select(func.min(Change.id)))
        stamps = {x.key: x.value for x in (await db.scalars(select(Setting).where(Setting.key.in_(["calendars_changed_at", "settings_changed_at"])))).all()}
        now_meta = max(stamps.values(), default="0")  # "0" = noch nie geändert (nicht leer, damit die App es sich merkt)
        # Kalender/Einstellungen geändert (Farben, Feiertage …) → alles neu, das ist selten
        full = (since is None or since > cursor or (oldest is not None and since < oldest - 1) or (bool(meta_at) and meta_at < now_meta))
        ids: set[str] = set()
        if not full:
            ids = set((await db.scalars(select(Change.event_id).where(Change.id > since).distinct())).all())
            if len(ids) > MAX_CHANGED:
                full = True
        upserts, removed = [], []
        if full:
            upserts = await query.occurrences(db, a, b, c.by)
        else:
            cals = {x.id: x for x in (await db.scalars(select(Calendar))).all()}
            for eid in sorted(ids):
                ev = await db.get(Event, eid)
                cal = cals.get(ev.calendar_id) if ev else None
                mine = [query.occ_view(ev, occ, cal) for occ in rec.expand(ev, a, b)] if ev and ev.deleted_at is None and cal and store.readable(cal, c.by) else []
                upserts += mine
                if not mine:
                    removed.append(eid)   # gelöscht, nicht mehr lesbar oder (jetzt) außerhalb des Zeitraums: die App entfernt seine Vorkommen
        send_meta = full or not meta_at
        calendars = await cal_api.payload(db, c.by) if send_meta else None
        settings = await cfg.get_all(db) if send_meta else None
        return {"cursor": cursor, "full": full, "from": a.astimezone(local_now().tzinfo).date().isoformat(), "to": b.astimezone(local_now().tzinfo).date().isoformat(),
                "upserts": upserts, "removed_events": sorted(removed), "calendars": calendars, "settings": settings, "meta_at": now_meta}
