"""ICS: Export, Import und Abo-Feed (Paket `icalendar`). Alles lokal, nichts wird abgeholt."""
from datetime import date, datetime, time, timedelta, timezone

from icalendar import Alarm, Calendar as ICal
from icalendar import Event as IEvent
from icalendar import vRecur
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import new_id, utcnow
from ..errors import Invalid
from ..models import Calendar, Event
from . import recurrence as rec
from . import store
from .timeutil import UTC, zone

PRODID = "-//IDA Kalender//DE"


# ---------------- Export ----------------
def _rrule_out(ev: Event) -> str:
    p = rec.parse(ev.rrule)
    if "UNTIL" in p:
        local = datetime.strptime(p["UNTIL"], "%Y%m%dT%H%M%S")
        if ev.all_day:
            p["UNTIL"] = local.strftime("%Y%m%d")
        else:
            p["UNTIL"] = local.replace(tzinfo=zone(ev.tzid), fold=0).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return rec.build(p)


def _vevent(ev: Event, parent: Event | None) -> IEvent:
    zi = zone(ev.tzid)
    v = IEvent()
    v.add("uid", (parent or ev).uid)
    v.add("dtstamp", utcnow())
    v.add("created", ev.created_at)
    v.add("last-modified", ev.updated_at)
    v.add("sequence", ev.sequence or 0)
    v.add("summary", ev.title)
    if ev.all_day:
        v.add("dtstart", ev.start_at.astimezone(zi).date())
        v.add("dtend", ev.end_at.astimezone(zi).date())
    else:
        v.add("dtstart", ev.start_at.astimezone(zi))
        v.add("dtend", ev.end_at.astimezone(zi))
    if parent and ev.recurrence_original_start:
        orig = rec.parse_key(ev.recurrence_original_start)
        v.add("recurrence-id", orig.date() if ev.all_day else orig.replace(tzinfo=zi, fold=0))
    if ev.rrule and not parent:
        v.add("rrule", vRecur.from_ical(_rrule_out(ev)))
        if ev.exdates:
            ex = [rec.parse_key(k) for k in ev.exdates]
            v.add("exdate", [d.date() for d in ex] if ev.all_day else [d.replace(tzinfo=zi, fold=0) for d in ex])
    if ev.description:
        v.add("description", ev.description)
    if ev.location:
        v.add("location", ev.location)
    if ev.url:
        v.add("url", ev.url)
    v.add("status", {"confirmed": "CONFIRMED", "tentative": "TENTATIVE", "cancelled": "CANCELLED"}.get(ev.status, "CONFIRMED"))
    v.add("transp", "TRANSPARENT" if ev.transparency == "free" else "OPAQUE")
    if ev.color:
        v.add("x-ida-color", ev.color)
    if ev.travel_min:
        v.add("x-ida-travel-min", ev.travel_min)
    for m in ev.alarms or []:
        a = Alarm()
        a.add("action", "DISPLAY")
        a.add("description", ev.title)
        a.add("trigger", timedelta(minutes=-m))
        v.add_component(a)
    return v


async def export(db: AsyncSession, calendar_ids: set[str] | None = None, name: str = "IDA Kalender") -> bytes:
    cal = ICal()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", get_settings().timezone)
    cals = {c.id: c for c in (await db.scalars(select(Calendar))).all()}
    q = select(Event).where(Event.deleted_at.is_(None)).order_by(Event.start_at)
    evs = (await db.scalars(q)).all()
    by_id = {e.id: e for e in evs}
    for ev in evs:
        c = cals.get(ev.calendar_id)
        if not c or c.kind == "holidays" or (calendar_ids and ev.calendar_id not in calendar_ids):
            continue
        parent = by_id.get(ev.recurrence_parent_id) if ev.recurrence_parent_id else None
        if ev.recurrence_parent_id and not parent:
            continue
        cal.add_component(_vevent(ev, parent))
    try:
        cal.add_missing_timezones()
    except Exception:  # noqa: BLE001 – ohne VTIMEZONE verstehen die meisten Programme die bekannten Namen trotzdem
        pass
    return cal.to_ical()


# ---------------- Import ----------------
def _dt(prop):
    return prop.dt if prop is not None else None


def _tzid_of(dt) -> str:
    tz = getattr(dt, "tzinfo", None)
    key = getattr(tz, "key", None) or getattr(tz, "zone", None)
    if key and key.upper() != "UTC":
        try:
            zone(key)
            return key
        except Invalid:
            pass
    return get_settings().timezone


def _alarm_minutes(comp) -> list[int]:
    out = []
    for a in comp.walk("VALARM"):
        t = _dt(a.get("TRIGGER"))
        if isinstance(t, timedelta):
            out.append(max(0, int(-t.total_seconds() // 60)))
    return sorted(set(out))


def _exkeys(comp, tzid: str) -> list[str]:
    ex = comp.get("EXDATE")
    items = ex if isinstance(ex, list) else ([ex] if ex else [])
    keys = []
    for x in items:
        for d in x.dts:
            v = d.dt
            if isinstance(v, datetime):
                keys.append(rec.key_of(v.astimezone(zone(tzid)).replace(tzinfo=None) if v.tzinfo else v))
            else:
                keys.append(rec.key_of(datetime.combine(v, time.min)))
    return sorted(set(keys))


def _status(comp) -> str:
    return {"TENTATIVE": "tentative", "CANCELLED": "cancelled"}.get(str(comp.get("STATUS", "")).upper(), "confirmed")


def _fields(comp, default_alarms: bool = False) -> dict:
    ds = _dt(comp.get("DTSTART"))
    if ds is None:
        raise Invalid("Termin ohne Anfang (DTSTART)")
    all_day = isinstance(ds, date) and not isinstance(ds, datetime)
    de = _dt(comp.get("DTEND"))
    dur = _dt(comp.get("DURATION"))
    if de is None:
        if isinstance(dur, timedelta):
            de = ds + dur
        else:
            de = ds + timedelta(days=1) if all_day else ds + timedelta(hours=1)
    tzid = _tzid_of(ds)
    rr = comp.get("RRULE")
    return {
        "title": str(comp.get("SUMMARY", "") or "Ohne Titel")[:300], "description": str(comp.get("DESCRIPTION", "") or ""),
        "location": str(comp.get("LOCATION", "") or ""), "url": str(comp.get("URL", "") or ""),
        "start": ds, "end": de, "all_day": all_day, "tzid": tzid,
        "rrule": rec.normalize(rr.to_ical().decode(), tzid) if rr else "",
        "alarms": _alarm_minutes(comp), "status": _status(comp),
        "transparency": "free" if str(comp.get("TRANSP", "")).upper() == "TRANSPARENT" else "busy",
        "color": (str(comp.get("X-IDA-COLOR")) if comp.get("X-IDA-COLOR") else None),
        "travel_min": int(comp.get("X-IDA-TRAVEL-MIN") or 0),
    }


async def import_(db: AsyncSession, data: bytes, calendar_ref: str | None = None, by: str = "user", via: str = "ics") -> dict:
    """Termine aus einer .ics-Datei. Gleiche UID: aktualisieren, wenn die Datei neuer ist (SEQUENCE bzw. LAST-MODIFIED), sonst überspringen."""
    try:
        cal = ICal.from_ical(data)
    except Exception as e:  # noqa: BLE001
        raise Invalid(f"Keine gültige ICS-Datei: {str(e)[:120]}") from e
    target = await store.calendar_for(db, calendar_ref, by, write=True)
    comps = [c for c in cal.walk("VEVENT")]
    base = [c for c in comps if c.get("RECURRENCE-ID") is None]
    exc = [c for c in comps if c.get("RECURRENCE-ID") is not None]
    res = {"neu": 0, "aktualisiert": 0, "uebersprungen": 0, "fehler": []}
    batch = new_id()
    by_uid: dict[str, Event] = {}
    for comp in base:
        uid = str(comp.get("UID") or f"{new_id()}{new_id()}@import")
        try:
            f = _fields(comp)
            seq = int(comp.get("SEQUENCE") or 0)
            lm = _dt(comp.get("LAST-MODIFIED"))
            ev = await db.scalar(select(Event).where(Event.uid == uid))
            if ev is None:
                ev = await store.create_event(db, {**f, "uid": uid, "calendar_id": target.id, "exdates": None}, by, via, batch)
                ev.exdates = _exkeys(comp, f["tzid"])
                ev.sequence = seq
                res["neu"] += 1
            else:
                newer = seq > (ev.sequence or 0) or (isinstance(lm, datetime) and lm.astimezone(UTC) > ev.updated_at and seq == (ev.sequence or 0))
                if not newer and ev.deleted_at is None:
                    res["uebersprungen"] += 1
                    by_uid[uid] = ev
                    continue
                before = store.snapshot(ev)
                patch = {k: v for k, v in f.items() if k != "tzid"}
                ev.tzid = f["tzid"]
                ev.deleted_at = None
                await store._apply_patch(db, ev, patch, by)  # noqa: SLF001
                ev.exdates = _exkeys(comp, f["tzid"])
                ev.sequence = max(seq, ev.sequence or 0)
                await store._record(db, ev, "update", before, by, via, batch)  # noqa: SLF001
                res["aktualisiert"] += 1
            by_uid[uid] = ev
        except Invalid as e:
            res["fehler"].append(f"{str(comp.get('SUMMARY', ''))[:40]}: {e}")
    for comp in exc:
        uid = str(comp.get("UID") or "")
        parent = by_uid.get(uid) or await db.scalar(select(Event).where(Event.uid == uid))
        if parent is None or not parent.rrule:
            res["uebersprungen"] += 1
            continue
        try:
            rid = _dt(comp.get("RECURRENCE-ID"))
            key = rec.key_of(rid.astimezone(zone(parent.tzid)).replace(tzinfo=None) if isinstance(rid, datetime) and rid.tzinfo
                             else (rid if isinstance(rid, datetime) else datetime.combine(rid, time.min)))
            f = _fields(comp)
            f.pop("rrule", None)
            f.update({"uid": f"{parent.uid}#{key}", "calendar_id": parent.calendar_id, "tzid": parent.tzid})
            old = await db.scalar(select(Event).where(Event.uid == f["uid"]))
            if old is not None:
                res["uebersprungen"] += 1
                continue
            e = await store.create_event(db, {**f, "exdates": None}, by, via, batch)
            e.recurrence_parent_id, e.recurrence_original_start = parent.id, key
            parent.exdates = sorted(set(parent.exdates or []) | {key})
            res["neu"] += 1
        except Invalid as ex:
            res["fehler"].append(f"{str(comp.get('SUMMARY', ''))[:40]}: {ex}")
    return res
