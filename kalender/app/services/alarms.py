"""Erinnerungen: fällige Alarme finden, genau einmal auslösen (Live-Fenster + Warteschlange an IDA)."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import String, cast, select

from ..config import get_settings
from ..db import pending, session_scope, utcnow
from ..models import AlarmSent, Calendar, Event, Outbox
from . import recurrence as rec
from .timeutil import iso

log = logging.getLogger("kalender.alarms")
GRACE = timedelta(minutes=15)   # nach einer Pause werden nur Alarme der letzten 15 Minuten nachgeholt


def payload(ev: Event, occ: rec.Occ, cal: Calendar, minutes: int) -> dict:
    return {"type": "alarm", "event_id": ev.id, "occurrence_id": rec.occurrence_id(ev.id, ev, occ), "title": ev.title, "calendar": cal.name,
            "color": ev.color or cal.color, "start": iso(occ.start, ev.tzid) if not ev.all_day else occ.start.astimezone(rec.zone(ev.tzid)).date().isoformat(),
            "end": iso(occ.end, ev.tzid) if not ev.all_day else occ.end.astimezone(rec.zone(ev.tzid)).date().isoformat(),
            "all_day": ev.all_day, "location": ev.location, "minutes": minutes, "action": ev.alarm_action, "task": ev.alarm_task}


async def tick(now: datetime | None = None) -> int:
    """Löst alle fälligen Erinnerungen aus. Der Primärschlüssel von alarms_sent verhindert doppeltes Auslösen. → Anzahl."""
    now = now or utcnow()
    fired = 0
    hook = bool(get_settings().ida_webhook_url)
    async with session_scope(write=True) as db:
        cals = {c.id: c for c in (await db.scalars(select(Calendar))).all()}
        q = select(Event).where(Event.deleted_at.is_(None), Event.status != "cancelled", cast(Event.alarms, String) != "[]")
        for ev in (await db.scalars(q)).all():
            cal = cals.get(ev.calendar_id)
            if cal is None or not ev.alarms:
                continue
            if not ev.rrule and ev.end_at <= now:
                continue
            if ev.series_end is not None and ev.series_end <= now - GRACE:
                continue
            horizon = now + timedelta(minutes=max(ev.alarms) + 1)
            for occ in rec.expand(ev, now - GRACE, horizon):
                for m in ev.alarms:
                    fire_at = occ.start - timedelta(minutes=m)
                    if not (now - GRACE <= fire_at <= now) or occ.end <= now:
                        continue
                    if await db.get(AlarmSent, (ev.id, occ.key, m)):
                        continue
                    db.add(AlarmSent(event_id=ev.id, occurrence_start=occ.key, minutes=m))
                    p = payload(ev, occ, cal, m)
                    pending(db).append(p)
                    if hook:
                        db.add(Outbox(kind="alarm", payload=p))
                    fired += 1
    if fired:
        log.info("%d Erinnerung(en) ausgelöst", fired)
    return fired


async def cleanup(days: int = 3) -> None:
    """Alte Merker löschen (die Termine sind lange vorbei)."""
    from sqlalchemy import delete
    async with session_scope(write=True) as db:
        await db.execute(delete(AlarmSent).where(AlarmSent.sent_at < utcnow() - timedelta(days=days)))
