"""Termine anlegen, ändern, löschen (Papierkorb), Verlauf und Rückgängig.

Jede Schreibfunktion schreibt Zeilen in `changes`, pflegt den Suchindex und kündigt ein Live-Ereignis an
(`pending`; verschickt wird es von `session_scope` nach dem Commit). Rechte der Agents werden hier geprüft.
"""
import copy
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from .. import palette
from ..config import get_settings
from ..db import new_id, pending, utcnow
from ..errors import Forbidden, Invalid, NotFound
from ..models import Calendar, Change, Event
from . import recurrence as rec
from . import search
from .timeutil import UTC, parse_input, zone

_SNAP = ["calendar_id", "uid", "title", "description", "location", "url", "start_at", "end_at", "all_day", "tzid", "rrule", "exdates",
         "series_end", "recurrence_parent_id", "recurrence_original_start", "status", "transparency", "color", "alarms", "alarm_action",
         "alarm_task", "travel_min", "birth_year", "attendees", "created_by", "updated_by", "sequence", "deleted_at"]
_DT = {"start_at", "end_at", "series_end", "deleted_at"}
MAX_ALARM = 60 * 24 * 28


# ---------------- Rechte ----------------
def is_agent(by: str) -> bool:
    return by != "user"


def writable(cal: Calendar, by: str) -> bool:
    return cal.kind != "holidays" and (not is_agent(by) or cal.agent_access == "write")


def readable(cal: Calendar, by: str) -> bool:
    return not is_agent(by) or cal.agent_access != "none"


async def calendar_for(db: AsyncSession, ref: str | None, by: str, write: bool = False) -> Calendar:
    """Kalender per ID oder Name; ohne Angabe der Standardkalender."""
    cal = None
    if ref:
        cal = await db.get(Calendar, ref) or await db.scalar(select(Calendar).where(Calendar.name.ilike(str(ref).strip())))
        if not cal:
            raise NotFound(f"Kalender „{ref}“ gibt es nicht")
    else:
        cal = (await db.scalar(select(Calendar).where(Calendar.is_default.is_(True)))) or \
              (await db.scalar(select(Calendar).where(Calendar.kind == "normal").order_by(Calendar.position)))
        if not cal:
            raise Invalid("Es gibt noch keinen Kalender")
    if not readable(cal, by):
        raise NotFound(f"Kalender „{ref}“ gibt es nicht")
    if write and not writable(cal, by):
        if cal.kind == "holidays":
            raise Forbidden("Feiertage sind schreibgeschützt")
        raise Forbidden(f"Der Kalender „{cal.name}“ ist für Agents nur lesbar – das stellt der Nutzer in den Kalender-Einstellungen ein")
    return cal


async def get_event(db: AsyncSession, event_id: str, by: str = "user", write: bool = False, deleted: bool = False) -> Event:
    ev = await db.get(Event, event_id)
    if not ev or (ev.deleted_at is not None and not deleted):
        raise NotFound("Termin nicht gefunden")
    cal = await db.get(Calendar, ev.calendar_id)
    if not cal or not readable(cal, by):
        raise NotFound("Termin nicht gefunden")
    if write and not writable(cal, by):
        raise Forbidden(f"Der Kalender „{cal.name}“ ist für Agents nur lesbar")
    return ev


# ---------------- Schnappschüsse ----------------
def snapshot(ev: Event) -> dict:
    out = {}
    for k in _SNAP:
        v = getattr(ev, k)
        out[k] = v.astimezone(UTC).isoformat() if k in _DT and v is not None else copy.deepcopy(v)
    return out


def restore_snapshot(ev: Event, snap: dict) -> None:
    for k in _SNAP:
        if k in snap:
            v = snap[k]
            if k in _DT and v is not None:
                v = datetime.fromisoformat(v)
            setattr(ev, k, copy.deepcopy(v))


async def _record(db: AsyncSession, ev: Event, action: str, before: dict | None, by: str, via: str, batch: str) -> Change:
    chg = Change(batch=batch, event_id=ev.id, calendar_id=ev.calendar_id, action=action, by=by, via=via, title=ev.title[:300],
                 before=before, after=snapshot(ev))
    db.add(chg)
    await db.flush()
    await search.index(db, ev)
    pending(db).append({"type": "changed", "ids": [ev.id], "calendar_ids": [ev.calendar_id], "by": by, "via": via,
                        "action": action, "change_id": chg.id, "title": ev.title[:120]})
    return chg


# ---------------- Eingaben prüfen ----------------
def _clean_alarms(v) -> list[int]:
    if v is None:
        return []
    if isinstance(v, (int, float)):
        v = [v]
    try:
        out = sorted({int(x) for x in v})
    except (TypeError, ValueError) as e:
        raise Invalid("Erinnerungen: Minuten vor dem Termin als Zahlen, z. B. [10, 1440]") from e
    if any(x < 0 or x > MAX_ALARM for x in out):
        raise Invalid("Erinnerungen: 0 bis 40320 Minuten (4 Wochen) vorher")
    return out


def _times(data: dict, tzid: str, cur: Event | None = None, occ_span: tuple[datetime, datetime] | None = None) -> tuple[datetime, datetime, bool]:
    """start/end/all_day/duration_min aus einer Eingabe → (start_at, end_at, all_day) in UTC.

    Ganztägig: `end` ist exklusiv (der Tag NACH dem letzten Tag). Fehlt `end`, bleibt die Dauer bzw. gilt ein Tag/eine Stunde.
    """
    zi = zone(tzid)
    base_s, base_e, base_all = (occ_span[0], occ_span[1], cur.all_day) if (cur is not None and occ_span) else \
        ((cur.start_at, cur.end_at, cur.all_day) if cur is not None else (None, None, False))
    all_day = bool(data["all_day"]) if data.get("all_day") is not None else None
    start = end = None
    start_is_date = end_is_date = False
    if data.get("start") not in (None, ""):
        start, start_is_date = parse_input(data["start"], tzid)
    if data.get("end") not in (None, ""):
        end, end_is_date = parse_input(data["end"], tzid)
    if all_day is None:
        all_day = start_is_date if start is not None else base_all
    if start is None:
        if base_s is None:
            raise Invalid("Start fehlt (start: JJJJ-MM-TT oder JJJJ-MM-TTTHH:MM)")
        start_keep = True
        start = base_s
    else:
        start_keep = False
    if all_day:
        s_date = start.astimezone(zi).date()
        s = datetime.combine(s_date, time.min).replace(tzinfo=zi, fold=0).astimezone(UTC)
        if end is not None:
            e_date = end.astimezone(zi).date()
            if not end_is_date and e_date == s_date and end.astimezone(zi).time() != time.min:
                e_date = s_date + timedelta(days=1)  # „bis 17 Uhr“ bei ganztägig: derselbe Tag
        elif base_s is not None and base_all:
            days = max(1, (base_e.astimezone(zi).date() - base_s.astimezone(zi).date()).days)
            e_date = s_date + timedelta(days=days)
        else:
            e_date = s_date + timedelta(days=1)
        if e_date <= s_date:
            raise Invalid("Das Ende liegt nicht nach dem Anfang (bei ganztägigen Terminen ist das Ende der Tag NACH dem letzten Tag)")
        return s, datetime.combine(e_date, time.min).replace(tzinfo=zi, fold=0).astimezone(UTC), True
    if start_is_date and not start_keep:
        raise Invalid("Für eine Uhrzeit start als JJJJ-MM-TTTHH:MM angeben (oder all_day=true)")
    if end is not None:
        e = end
    elif data.get("duration_min") not in (None, ""):
        try:
            mins = int(data["duration_min"])
        except (TypeError, ValueError) as e2:
            raise Invalid("duration_min muss eine Zahl sein") from e2
        if mins < 1:
            raise Invalid("duration_min muss mindestens 1 sein")
        e = start + timedelta(minutes=mins)
    elif base_s is not None and not base_all:
        e = start + (base_e - base_s)  # Dauer bleibt beim Verschieben
    else:
        e = start + timedelta(hours=1)
    if e <= start:
        raise Invalid("Das Ende liegt nicht nach dem Anfang")
    if e - start > timedelta(days=366):
        raise Invalid("Ein Termin darf höchstens ein Jahr dauern")
    return start, e, False


def _rrule_from(data: dict, tzid: str) -> str | None:
    """None = unverändert, "" = keine Wiederholung."""
    if "rrule" in data and data["rrule"] is not None:
        return rec.normalize(str(data["rrule"]), tzid)
    rp = data.get("repeat")
    if rp is None:
        return None
    if isinstance(rp, str):
        return rec.build_rrule(rp)
    if isinstance(rp, dict):
        if not rp.get("freq") and not rp.get("repeat"):
            return ""
        return rec.build_rrule(rp.get("freq") or rp.get("repeat"), rp.get("interval") or 1, rp.get("days"), rp.get("until"), rp.get("count"), rp.get("nth"))
    raise Invalid("repeat: Text (daily, weekly, …) oder Objekt {freq, interval, days, until, count}")


def _simple(data: dict) -> dict:
    """Die einfachen Felder prüfen; nur was in `data` steht."""
    out = {}
    if "title" in data:
        t = str(data["title"] or "").strip()
        if not t:
            raise Invalid("Titel fehlt")
        out["title"] = t[:300]
    for k, n in (("description", 20000), ("location", 300), ("url", 500), ("alarm_task", 500)):
        if k in data and data[k] is not None:
            out[k] = str(data[k])[:n]
    if "color" in data:
        c = data["color"] or None
        if c is not None and c not in palette.KEYS:
            raise Invalid(f"Unbekannte Farbe „{c}“ ({', '.join(palette.KEYS)})")
        out["color"] = c
    if data.get("status") is not None:
        if data["status"] not in ("confirmed", "tentative", "cancelled"):
            raise Invalid("status: confirmed, tentative oder cancelled")
        out["status"] = data["status"]
    if data.get("transparency") is not None:
        if data["transparency"] not in ("busy", "free"):
            raise Invalid("transparency: busy oder free")
        out["transparency"] = data["transparency"]
    if data.get("alarm_action") is not None:
        if data["alarm_action"] not in ("notify", "agent"):
            raise Invalid("alarm_action: notify oder agent")
        out["alarm_action"] = data["alarm_action"]
    if data.get("travel_min") is not None:
        try:
            tm = int(data["travel_min"])
        except (TypeError, ValueError) as e:
            raise Invalid("travel_min muss eine Zahl sein") from e
        if not 0 <= tm <= 480:
            raise Invalid("travel_min: 0 bis 480")
        out["travel_min"] = tm
    if "birth_year" in data:
        out["birth_year"] = int(data["birth_year"]) if data["birth_year"] else None
    if "attendees" in data and data["attendees"] is not None:
        out["attendees"] = [str(a)[:80] for a in data["attendees"]][:30]
    if "alarms" in data and data["alarms"] is not None:
        out["alarms"] = _clean_alarms(data["alarms"])
    return out


def _stamp(ev: Event, by: str) -> None:
    ev.updated_by = by
    ev.sequence = (ev.sequence or 0) + 1
    ev.updated_at = utcnow()


def _refresh_series(ev: Event) -> None:
    ev.series_end = rec.series_end(ev.rrule, ev.start_at, ev.end_at, ev.tzid, ev.all_day) if ev.rrule else None


# ---------------- Anlegen ----------------
async def create_event(db: AsyncSession, data: dict, by: str = "user", via: str = "ui", batch: str | None = None) -> Event:
    if data.get("uid"):  # die App vergibt die uid beim Offline-Anlegen: dieselbe uid = derselbe Termin, nicht noch einmal anlegen
        old = await db.scalar(select(Event).where(Event.uid == str(data["uid"])[:120]))
        if old is not None:
            old._existing = True  # noqa: SLF001 – die API meldet es als `existing`
            return old
    cal = await calendar_for(db, data.get("calendar_id") or data.get("calendar"), by, write=True)
    if cal.kind == "birthdays" and not data.get("rrule") and not data.get("repeat"):
        data = {**data, "rrule": "FREQ=YEARLY", "all_day": True}
    tzid = data.get("tzid") or get_settings().timezone
    zone(tzid)
    start, end, all_day = _times(data, tzid)
    rr = _rrule_from(data, tzid) or ""
    f = _simple(data)
    if not f.get("title"):
        raise Invalid("Titel fehlt")
    ev = Event(calendar_id=cal.id, uid=data.get("uid") or f"{new_id()}{new_id()}@ida-kalender", title=f["title"], description="", location="", url="",
               start_at=start, end_at=end, all_day=all_day, tzid=tzid, rrule=rr, exdates=[], status="confirmed", transparency="busy",
               color=None, alarms=list(cal.default_alarms or []), alarm_action="notify", alarm_task="", travel_min=0, attendees=[],
               created_by=by, updated_by=by, sequence=0)
    for k, v in f.items():
        setattr(ev, k, v)
    _refresh_series(ev)
    db.add(ev)
    await db.flush()
    await _record(db, ev, "create", None, by, via, batch or new_id())
    return ev


# ---------------- Ändern ----------------
def _occ_span(ev: Event, key: str) -> tuple[datetime, datetime]:
    zi = zone(ev.tzid)
    return rec._span(ev, rec.parse_key(key), zi)  # noqa: SLF001


def _check_key(ev: Event, key: str) -> None:
    first = rec.key_of(rec.local_start(ev.start_at, ev.tzid))
    if key in (ev.exdates or []):
        raise NotFound("Dieses Vorkommen gibt es nicht mehr")
    if key != first:
        # Prüfen, ob die Regel an diesem Tag überhaupt einen Termin hat
        occ = rec.expand(ev, rec.to_utc(rec.parse_key(key), ev.tzid) - timedelta(minutes=1), rec.to_utc(rec.parse_key(key), ev.tzid) + timedelta(minutes=1))
        if not any(o.key == key for o in occ):
            raise NotFound("Dieses Vorkommen gibt es nicht")


def _copy_fields(src: Event) -> dict:
    return {k: copy.deepcopy(getattr(src, k)) for k in ("calendar_id", "title", "description", "location", "url", "all_day", "tzid", "status", "transparency",
                                                        "color", "alarms", "alarm_action", "alarm_task", "travel_min", "birth_year", "attendees")}


async def _apply_patch(db: AsyncSession, ev: Event, patch: dict, by: str, occ_span: tuple[datetime, datetime] | None = None,
                       allow_rule: bool = True) -> None:
    f = _simple(patch)
    if "calendar_id" in patch or "calendar" in patch:
        cal = await calendar_for(db, patch.get("calendar_id") or patch.get("calendar"), by, write=True)
        f["calendar_id"] = cal.id
    if any(k in patch for k in ("start", "end", "all_day", "duration_min")):
        ev.start_at, ev.end_at, ev.all_day = _times(patch, ev.tzid, ev, occ_span)
    elif occ_span:
        ev.start_at, ev.end_at = occ_span
    rr = _rrule_from(patch, ev.tzid)
    if rr is not None:
        if not allow_rule:
            raise Invalid("Die Wiederholung ändert man für die ganze Serie bzw. „dieser und folgende“, nicht für einen einzelnen Termin")
        ev.rrule = rr
        if not rr:
            ev.exdates = []
    for k, v in f.items():
        setattr(ev, k, v)
    _stamp(ev, by)
    _refresh_series(ev)


async def update_occurrence(db: AsyncSession, occ_id: str, patch: dict, scope: str = "all", by: str = "user", via: str = "ui") -> list[Event]:
    """Gibt die geänderten/neuen Termine zurück. scope: this | following | all (nur bei Serien wichtig)."""
    if scope not in ("this", "following", "all"):
        raise Invalid("scope: this, following oder all")
    eid, key = rec.split_id(occ_id)
    ev = await get_event(db, eid, by, write=True)
    batch = new_id()
    if ev.recurrence_parent_id or not ev.rrule:
        # einfacher Termin oder bereits einzeln geänderter Termin
        before = snapshot(ev)
        await _apply_patch(db, ev, patch, by, allow_rule=not ev.recurrence_parent_id)
        await _record(db, ev, "update", before, by, via, batch)
        return [ev]
    if key is None:
        key = rec.key_of(rec.local_start(ev.start_at, ev.tzid))
        if scope == "this":
            scope = "all"
    _check_key(ev, key)
    first_key = rec.key_of(rec.local_start(ev.start_at, ev.tzid))
    if scope == "following" and key == first_key:
        scope = "all"
    span = _occ_span(ev, key)
    if scope == "this":
        parent_before = snapshot(ev)
        exc = Event(uid=f"{ev.uid}#{key}", recurrence_parent_id=ev.id, recurrence_original_start=key, rrule="", exdates=[], series_end=None,
                    start_at=span[0], end_at=span[1], created_by=by, updated_by=by, sequence=0, **_copy_fields(ev))
        db.add(exc)
        await db.flush()
        await _apply_patch(db, exc, patch, by, allow_rule=False)
        ev.exdates = sorted(set(ev.exdates or []) | {key})
        _stamp(ev, by)
        await _record(db, ev, "update", parent_before, by, via, batch)
        await _record(db, exc, "create", None, by, via, batch)
        return [exc, ev]
    if scope == "all":
        before = snapshot(ev)
        if any(k in patch for k in ("start", "end", "all_day", "duration_min")):
            # Die Eingabe gilt für DIESES Vorkommen; die ganze Serie rückt um denselben Abstand bzw. bekommt dieselbe Dauer
            zi = zone(ev.tzid)
            new_s, new_e, new_all = _times(patch, ev.tzid, ev, span)
            delta = new_s.astimezone(zi).replace(tzinfo=None) - span[0].astimezone(zi).replace(tzinfo=None)
            base_local = rec.local_start(ev.start_at, ev.tzid) + delta
            if new_all:
                ndays = max(1, (new_e.astimezone(zi).date() - new_s.astimezone(zi).date()).days)
                ev.start_at = rec.to_utc(datetime.combine(base_local.date(), time.min), ev.tzid)
                ev.end_at = rec.to_utc(datetime.combine(base_local.date() + timedelta(days=ndays), time.min), ev.tzid)
            else:
                ev.start_at = rec.to_utc(base_local, ev.tzid)
                ev.end_at = ev.start_at + (new_e - new_s)
            ev.all_day = new_all
            if delta:
                ev.exdates = sorted(rec.key_of(rec.parse_key(k) + delta) for k in (ev.exdates or []))
                for ex in (await db.scalars(select(Event).where(Event.recurrence_parent_id == ev.id))).all():
                    if ex.recurrence_original_start:
                        ex.recurrence_original_start = rec.key_of(rec.parse_key(ex.recurrence_original_start) + delta)
            patch = {k: v for k, v in patch.items() if k not in ("start", "end", "all_day", "duration_min")}
        await _apply_patch(db, ev, patch, by)
        await _record(db, ev, "update", before, by, via, batch)
        return [ev]
    # following: die Serie vor diesem Vorkommen beenden, ab hier eine neue anlegen
    parent_before = snapshot(ev)
    old_rule, new_rule = rec.cut_until(ev.rrule, ev.start_at, ev.tzid, rec.parse_key(key))
    new = Event(uid=f"{new_id()}{new_id()}@ida-kalender", rrule="", exdates=[k for k in (ev.exdates or []) if k >= key], series_end=None,
                start_at=span[0], end_at=span[1], created_by=by, updated_by=by, sequence=0, **_copy_fields(ev))
    db.add(new)
    await db.flush()
    new.rrule = new_rule
    await _apply_patch(db, new, patch, by)
    ev.rrule = old_rule
    ev.exdates = [k for k in (ev.exdates or []) if k < key]
    _stamp(ev, by)
    _refresh_series(ev)
    for ex in (await db.scalars(select(Event).where(Event.recurrence_parent_id == ev.id, Event.recurrence_original_start >= key))).all():
        ex.recurrence_parent_id = new.id
    await _record(db, ev, "update", parent_before, by, via, batch)
    await _record(db, new, "create", None, by, via, batch)
    return [new, ev]


# ---------------- Löschen ----------------
async def delete_occurrence(db: AsyncSession, occ_id: str, scope: str = "all", by: str = "user", via: str = "ui") -> list[Event]:
    if scope not in ("this", "following", "all"):
        raise Invalid("scope: this, following oder all")
    eid, key = rec.split_id(occ_id)
    ev = await get_event(db, eid, by, write=True)
    batch = new_id()
    now = utcnow()
    if ev.rrule and not ev.recurrence_parent_id and key and scope in ("this", "following"):
        _check_key(ev, key)
        first_key = rec.key_of(rec.local_start(ev.start_at, ev.tzid))
        if scope == "following" and key == first_key:
            scope = "all"
        elif scope == "this":
            before = snapshot(ev)
            ev.exdates = sorted(set(ev.exdates or []) | {key})
            _stamp(ev, by)
            await _record(db, ev, "update", before, by, via, batch)
            return [ev]
        else:
            before = snapshot(ev)
            old_rule, _ = rec.cut_until(ev.rrule, ev.start_at, ev.tzid, rec.parse_key(key))
            ev.rrule = old_rule
            ev.exdates = [k for k in (ev.exdates or []) if k < key]
            _stamp(ev, by)
            _refresh_series(ev)
            await _record(db, ev, "update", before, by, via, batch)
            out = [ev]
            for ex in (await db.scalars(select(Event).where(Event.recurrence_parent_id == ev.id, Event.recurrence_original_start >= key,
                                                            Event.deleted_at.is_(None)))).all():
                b = snapshot(ex)
                ex.deleted_at = now
                _stamp(ex, by)
                await _record(db, ex, "delete", b, by, via, batch)
                out.append(ex)
            return out
    out = []
    for e in [ev] + list((await db.scalars(select(Event).where(Event.recurrence_parent_id == ev.id, Event.deleted_at.is_(None)))).all()):
        b = snapshot(e)
        e.deleted_at = now
        _stamp(e, by)
        await _record(db, e, "delete", b, by, via, batch)
        out.append(e)
    return out


async def restore(db: AsyncSession, event_id: str, by: str = "user", via: str = "ui") -> Event:
    ev = await get_event(db, event_id, by, write=True, deleted=True)
    if ev.deleted_at is None:
        raise Invalid("Der Termin liegt nicht im Papierkorb")
    stamp, batch = ev.deleted_at, new_id()
    for e in [ev] + list((await db.scalars(select(Event).where(Event.recurrence_parent_id == ev.id, Event.deleted_at == stamp))).all()):
        b = snapshot(e)
        e.deleted_at = None
        _stamp(e, by)
        await _record(db, e, "restore", b, by, via, batch)
    return ev


async def trash(db: AsyncSession, by: str = "user") -> list[Event]:
    rows = (await db.scalars(select(Event).where(Event.deleted_at.is_not(None), Event.recurrence_parent_id.is_(None))
                             .order_by(Event.deleted_at.desc()).limit(500))).all()
    cals = {c.id: c for c in (await db.scalars(select(Calendar))).all()}
    return [e for e in rows if e.calendar_id in cals and readable(cals[e.calendar_id], by)]


async def purge_trash(db: AsyncSession, older_than_days: int | None = None) -> int:
    q = select(Event.id).where(Event.deleted_at.is_not(None))
    if older_than_days is not None:
        q = q.where(Event.deleted_at < utcnow() - timedelta(days=older_than_days))
    ids = list((await db.scalars(q)).all())
    for i in ids:
        await search.unindex(db, i)
    if ids:
        await db.execute(delete(Event).where(Event.id.in_(ids)))
    return len(ids)


async def purge_changes(db: AsyncSession, days: int) -> int:
    r = await db.execute(delete(Change).where(Change.at < utcnow() - timedelta(days=days)))
    return r.rowcount or 0


# ---------------- Verlauf und Rückgängig ----------------
async def undo(db: AsyncSession, change_id: int, by: str = "user", via: str = "ui") -> dict:
    chg = await db.get(Change, change_id)
    if not chg:
        raise NotFound("Änderung nicht gefunden")
    if is_agent(by) and chg.by != by:
        raise Forbidden("Agents machen nur ihre eigenen Änderungen rückgängig")
    group = (await db.scalars(select(Change).where(Change.batch == chg.batch).order_by(Change.id.desc()))).all()
    if any(c.undone_at for c in group):
        raise Invalid("Diese Änderung ist schon rückgängig gemacht")
    batch, now, titles = new_id(), utcnow(), []
    for c in group:  # neueste zuerst
        ev = await db.get(Event, c.event_id)
        if not ev:
            raise Invalid("Der Termin existiert nicht mehr (Papierkorb geleert)")
        cal = await db.get(Calendar, ev.calendar_id)
        if cal and not writable(cal, by):
            raise Forbidden(f"Der Kalender „{cal.name}“ ist für Agents nur lesbar")
        before = snapshot(ev)
        if c.action == "create":
            ev.deleted_at = now
            action = "delete"
        else:
            restore_snapshot(ev, c.before or {})
            action = "restore" if (c.action == "delete") else "update"
        _stamp(ev, by)
        await _record(db, ev, action, before, by, via, batch)
        c.undone_at = now
        titles.append(ev.title)
    last = (await db.scalars(select(Change).where(Change.batch == batch).order_by(Change.id.desc()).limit(1))).first()
    return {"undone": [c.id for c in group], "change_id": last.id if last else None, "titles": titles, "action": group[-1].action}


def change_view(c: Change) -> dict:
    from .timeutil import iso
    return {"id": c.id, "batch": c.batch, "event_id": c.event_id, "calendar_id": c.calendar_id, "action": c.action, "by": c.by, "via": c.via,
            "title": c.title, "at": iso(c.at), "undone": c.undone_at is not None}


async def changes(db: AsyncSession, by: str | None = None, since: int | None = None, limit: int = 50, event_id: str | None = None) -> list[Change]:
    q = select(Change).order_by(Change.id.desc()).limit(max(1, min(limit, 500)))
    if by:
        q = q.where(Change.by == by)
    if since is not None:
        q = q.where(Change.id > since)
    if event_id:
        q = q.where(Change.event_id == event_id)
    return list((await db.scalars(q)).all())


async def last_change(db: AsyncSession, by: str) -> Change | None:
    return (await db.scalars(select(Change).where(Change.by == by, Change.undone_at.is_(None)).order_by(Change.id.desc()).limit(1))).first()
