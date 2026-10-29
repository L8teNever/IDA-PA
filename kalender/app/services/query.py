"""Termine im Zeitraum abfragen (Serien aufgelöst), freie Zeit, Konflikte, Tagesübersicht, Statistik."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..errors import Invalid
from ..models import Calendar, Event
from . import holidays as hol
from . import recurrence as rec
from . import settings as cfg
from .store import readable
from .timeutil import UTC, WEEKDAYS_LONG, day_bounds, fmt_when, iso, zone


async def _calendars(db: AsyncSession) -> dict[str, Calendar]:
    return {c.id: c for c in (await db.scalars(select(Calendar).order_by(Calendar.position))).all()}


def _day_str(dt: datetime, tzid: str) -> str:
    return dt.astimezone(zone(tzid)).date().isoformat()


def occ_view(ev: Event, occ: rec.Occ, cal: Calendar) -> dict:
    if ev.all_day:
        start, end = _day_str(occ.start, ev.tzid), _day_str(occ.end, ev.tzid)
    else:
        start, end = iso(occ.start), iso(occ.end)
    v = {"id": rec.occurrence_id(ev.id, ev, occ), "event_id": ev.id, "calendar_id": cal.id, "calendar": cal.name, "title": ev.title,
         "start": start, "end": end, "all_day": ev.all_day, "location": ev.location, "color": ev.color or cal.color,
         "recurring": bool(ev.rrule) or bool(ev.recurrence_parent_id), "recurrence_text": rec.describe(ev.rrule) if ev.rrule else "",
         "alarms": ev.alarms, "status": ev.status, "transparency": ev.transparency, "travel_min": ev.travel_min,
         "created_by": ev.created_by, "updated_by": ev.updated_by, "read_only": cal.kind == "holidays", "has_description": bool(ev.description)}
    if ev.birth_year:
        v["age"] = int(_day_str(occ.start, ev.tzid)[:4]) - ev.birth_year
    return v


def detail_view(ev: Event, cal: Calendar, occ: rec.Occ | None = None) -> dict:
    """Ein Termin mit allen Feldern (Detail-Sheet, Editor, calendar_event_get)."""
    occ = occ or rec.Occ(rec.key_of(rec.local_start(ev.start_at, ev.tzid)), ev.start_at, ev.end_at)
    if ev.rrule and occ.start == ev.start_at:
        pass
    v = occ_view(ev, occ, cal)
    v.update({"description": ev.description, "url": ev.url, "rrule": ev.rrule, "tzid": ev.tzid, "alarm_action": ev.alarm_action,
              "alarm_task": ev.alarm_task, "attendees": ev.attendees, "birth_year": ev.birth_year, "sequence": ev.sequence,
              "series_start": _day_str(ev.start_at, ev.tzid) if ev.rrule else None, "exdates": ev.exdates,
              "parent_id": ev.recurrence_parent_id, "uid": ev.uid, "deleted": ev.deleted_at is not None,
              "created_at": iso(ev.created_at), "updated_at": iso(ev.updated_at)})
    return v


async def raw_occurrences(db: AsyncSession, start: datetime, end: datetime, by: str = "user",
                          calendar_ids: set[str] | None = None) -> list[tuple[Event, rec.Occ, Calendar]]:
    cals = await _calendars(db)
    allowed = {i for i, c in cals.items() if readable(c, by) and c.kind != "holidays" and (not calendar_ids or i in calendar_ids)}
    if not allowed:
        return []
    q = select(Event).where(
        Event.deleted_at.is_(None), Event.calendar_id.in_(allowed),
        or_(and_(Event.rrule == "", Event.start_at < end, Event.end_at > start),
            and_(Event.rrule != "", Event.start_at < end, or_(Event.series_end.is_(None), Event.series_end > start))))
    out = []
    for ev in (await db.scalars(q)).all():
        for occ in rec.expand(ev, start, end):
            out.append((ev, occ, cals[ev.calendar_id]))
    out.sort(key=lambda x: (x[1].start, not x[0].all_day, x[1].end))
    return out


async def occurrences(db: AsyncSession, start: datetime, end: datetime, by: str = "user", calendar_ids: set[str] | None = None,
                      holidays: bool = True) -> list[dict]:
    if end - start > timedelta(days=800):
        raise Invalid("Der Zeitraum ist zu groß (höchstens gut zwei Jahre)")
    out = [occ_view(ev, occ, cal) for ev, occ, cal in await raw_occurrences(db, start, end, by, calendar_ids)]
    if holidays:
        cals = await _calendars(db)
        for c in cals.values():
            if c.kind == "holidays" and c.visible and readable(c, by) and (not calendar_ids or c.id in calendar_ids):
                state = (await cfg.get_all(db)).get("state") or get_settings().kalender_state
                out += hol.occurrences(start, end, state, c)
    out.sort(key=lambda v: (v["start"][:10], not v["all_day"], v["start"]))
    return out


# ---------------- Konflikte, freie Zeit ----------------
def _busy(ev: Event, occ: rec.Occ) -> tuple[datetime, datetime] | None:
    if ev.all_day or ev.transparency != "busy" or ev.status == "cancelled":
        return None
    return occ.start - timedelta(minutes=ev.travel_min or 0), occ.end


async def conflicts(db: AsyncSession, start: datetime, end: datetime, exclude_event_id: str | None = None, by: str = "user") -> list[dict]:
    out = []
    for ev, occ, cal in await raw_occurrences(db, start - timedelta(hours=8), end + timedelta(hours=8), by):
        if ev.id == exclude_event_id:
            continue
        b = _busy(ev, occ)
        if b and b[0] < end and b[1] > start:
            out.append(occ_view(ev, occ, cal))
    return out


def _within(text: str | None) -> tuple[time, time]:
    try:
        a, b = (text or "08:00-20:00").replace("–", "-").split("-")
        return time.fromisoformat(a.strip()), time.fromisoformat(b.strip().replace("24:00", "23:59"))
    except ValueError as e:
        raise Invalid("within: Uhrzeiten als „08:00-20:00“") from e


async def free_slots(db: AsyncSession, start: datetime, end: datetime, duration_min: int = 60, within: str | None = None,
                     days: list[int] | None = None, by: str = "user", limit: int = 40) -> list[dict]:
    """Lücken ≥ duration_min innerhalb des Tagesfensters. Ganztägige Termine blockieren nicht; Reisezeit schon."""
    if duration_min < 1:
        raise Invalid("duration_min muss mindestens 1 sein")
    if end - start > timedelta(days=62):
        raise Invalid("Zeitraum für die Suche nach freier Zeit: höchstens 62 Tage")
    w0, w1 = _within(within)
    busy = sorted(b for ev, occ, _ in await raw_occurrences(db, start, end, by) if (b := _busy(ev, occ)))
    merged: list[list[datetime]] = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    zi = zone()
    d = start.astimezone(zi).date()
    last = (end - timedelta(seconds=1)).astimezone(zi).date()
    now = datetime.now(UTC)
    out = []
    while d <= last and len(out) < limit:
        if days is None or d.weekday() in days:
            ws = max(datetime.combine(d, w0).replace(tzinfo=zi, fold=0).astimezone(UTC), start, now)
            we = min(datetime.combine(d, w1).replace(tzinfo=zi, fold=0).astimezone(UTC), end)
            cur = ws
            for s, e in merged:
                if e <= cur:
                    continue
                if s >= we:
                    break
                if s - cur >= timedelta(minutes=duration_min):
                    out.append((cur, s))
                cur = max(cur, e)
            if we - cur >= timedelta(minutes=duration_min):
                out.append((cur, we))
        d += timedelta(days=1)
    return [{"start": iso(s), "end": iso(e), "minutes": int((e - s).total_seconds() // 60), "text": fmt_when(s, e, False)} for s, e in out[:limit]]


async def day_brief(db: AsyncSession, d: date, by: str = "user") -> dict:
    a, b = day_bounds(d)
    items = await occurrences(db, a, b, by)
    now = datetime.now(zone())
    lines = []
    for v in items:
        if v["all_day"]:
            lines.append(f"ganztägig: {v['title']}" + (" (Feiertag)" if v["id"].startswith("hol_") else ""))
        else:
            s, e = datetime.fromisoformat(v["start"]), datetime.fromisoformat(v["end"])
            lines.append(f"{s:%H:%M}–{e:%H:%M} {v['title']}" + (f" ({v['location']})" if v["location"] else ""))
    nxt = next((v for v in items if not v["all_day"] and datetime.fromisoformat(v["start"]) > now), None) if d == now.date() else None
    free = await free_slots(db, a, b, 60, None, None, by, limit=5)
    return {"datum": d.isoformat(), "wochentag": WEEKDAYS_LONG[d.weekday()], "anzahl": len(items), "termine": items, "uebersicht": lines or ["Nichts geplant"],
            "naechster": nxt["title"] + " " + datetime.fromisoformat(nxt["start"]).strftime("%H:%M") if nxt else None,
            "freie_fenster": [f["text"] for f in free]}


async def stats(db: AsyncSession, start: datetime, end: datetime, group_by: str = "calendar", by: str = "user") -> dict:
    """Zeit je Kalender oder je Titel (ohne ganztägige Termine); Termine werden an den Grenzen abgeschnitten."""
    if group_by not in ("calendar", "title"):
        raise Invalid("group_by: calendar oder title")
    mins: dict[str, float] = defaultdict(float)
    cnt: dict[str, int] = defaultdict(int)
    for ev, occ, cal in await raw_occurrences(db, start, end, by):
        if ev.all_day or ev.status == "cancelled":
            continue
        s, e = max(occ.start, start), min(occ.end, end)
        k = cal.name if group_by == "calendar" else ev.title
        mins[k] += (e - s).total_seconds() / 60
        cnt[k] += 1
    rows = sorted(({"name": k, "minuten": int(v), "stunden": round(v / 60, 1), "anzahl": cnt[k]} for k, v in mins.items()), key=lambda r: -r["minuten"])
    return {"von": iso(start), "bis": iso(end), "gruppiert_nach": group_by, "gesamt_stunden": round(sum(mins.values()) / 60, 1), "zeilen": rows[:50]}


async def next_after(db: AsyncSession, ev: Event, now: datetime) -> tuple[rec.Occ | None, int]:
    """Nächstes Vorkommen eines Termins ab jetzt (für die Suche) und wie viele weitere im nächsten Jahr folgen."""
    if not ev.rrule:
        return (rec.Occ(rec.key_of(rec.local_start(ev.start_at, ev.tzid)), ev.start_at, ev.end_at), 0) if ev.end_at > now else (None, 0)
    occs = rec.expand(ev, now, now + timedelta(days=366))
    return (occs[0] if occs else None), max(0, len(occs) - 1)
