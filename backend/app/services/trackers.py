"""Trackers: daily things to tick off („Tablette morgens + abends“) – by the user in the app or by an agent via MCP.

A tracker has slots (e.g. "Morgens 08:00", "Abends 20:00"); each slot is ticked once per local day.
A slot with a time is followed up when it is still open at that time (at most once per slot and day):
  on_missed "notify":  a notification for the user (no AI, costs no limit)
  on_missed "routine": the linked routine runs with a note which slot is open (best a routine "on demand")
"""
import logging
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope, utcnow
from ..models import USER, Agent, Message, Routine, Tracker, TrackerCheck, TrackerLink
from ..models._types import new_id
from . import schedule
from .errors import Invalid, NotFound

log = logging.getLogger("ida.trackers")
ACTIONS = ("none", "notify", "routine")
MAX_TRACKERS = 50
MAX_SLOTS = 6
MAX_BACK = 60  # days a forgotten tick may be added later
RECENT = 14  # days shown as dots
GRACE = timedelta(hours=3)  # a slot is followed up only this long after its time (not after a restart in the evening)
KEY_RE = re.compile(r"^[a-z0-9]{1,12}$")
SOON = timedelta(hours=2)  # a timed slot counts as due this long before its time (link / tick without slot)
MAX_LINKS = 10
MAX_DURATION = 2 * 3600  # timer: at most 2 hours
REPEAT = timedelta(seconds=30)  # NFC tags are often read twice: the same link within this time ticks only once
LINK_BY = "link"


def local_today(now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(schedule.tz()).date()


def active_on(t: Tracker, d: date) -> bool:
    return not t.days or d.weekday() in t.days


def default_label(time: str | None) -> str:
    if not time:
        return "Erledigt"
    h = int(time[:2])
    return "Morgens" if h < 11 else "Mittags" if h < 14 else "Nachmittags" if h < 18 else "Abends"


def slot_text(s: dict) -> str:
    return f"{s['label']} ({s['time']})" if s.get("time") else s["label"]


def _clean_slots(raw, old: list[dict]) -> list[dict]:
    """[{"label", "time"}] or just ["08:00", "20:00"]; keys of existing slots are kept (matched by key, label or time)."""
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SLOTS:
        raise Invalid(f"Zeitfenster: 1–{MAX_SLOTS} (z. B. Morgens 08:00, Abends 20:00)")
    raw = [({"time": s} if schedule.TIME_RE.match(s.strip()) else {"label": s}) if isinstance(s, str) else s for s in raw]
    untimed = sum(1 for s in raw if isinstance(s, dict) and not s.get("time"))
    out, used, n = [], set(), 0
    for s in raw:
        if not isinstance(s, dict):
            raise Invalid("Zeitfenster: Objekte mit label und time (HH:MM, optional)")
        time = str(s.get("time") or "").strip() or None
        if time:
            time = schedule.clean_times([time])[0]
        else:
            n += 1
        # several slots without time („3× täglich, egal wann“): 1. Mal, 2. Mal, 3. Mal
        label = str(s.get("label") or "").strip() or (f"{n}. Mal" if not time and untimed > 1 else default_label(time))
        if len(label) > 30:
            raise Invalid("Name eines Zeitfensters: höchstens 30 Zeichen")
        key = str(s.get("key") or "")
        if not KEY_RE.match(key) or key in used:
            same = next((o for o in old if o["key"] not in used and (o["label"].lower() == label.lower() or (time and o.get("time") == time))), None)
            key = same["key"] if same else new_id()[:6]
        used.add(key)
        out.append({"key": key, "label": label, "time": time})
    return out


async def _clean(db: AsyncSession, f: dict, partial: bool, old: Tracker | None, owner: str | None) -> dict:
    v: dict = {}
    if not partial or "name" in f:
        n = str(f.get("name") or "").strip()
        if not n or len(n) > 60:
            raise Invalid("Name: 1–60 Zeichen")
        v["name"] = n
    if f.get("icon"):
        v["icon"] = str(f["icon"])[:40]
    if f.get("note") is not None:
        v["note"] = str(f["note"]).strip()[:500]
    if not partial or f.get("slots") is not None:
        v["slots"] = _clean_slots(f.get("slots") or [{}], old.slots if old else [])
    if f.get("duration_s") is not None:
        try:
            dur = int(f["duration_s"])
        except (TypeError, ValueError) as e:
            raise Invalid("Dauer: Sekunden als ganze Zahl") from e
        if dur and not 10 <= dur <= MAX_DURATION:
            raise Invalid("Dauer: 0 (ohne Timer) oder 10 Sekunden bis 2 Stunden")
        v["duration_s"] = dur
    if "days" in f:
        d = schedule.clean_days(f["days"])
        v["days"] = None if len(d) == 7 else d
    if f.get("on_missed") is not None:
        if f["on_missed"] not in ACTIONS:
            raise Invalid("on_missed: none (nichts), notify (Benachrichtigung) oder routine (Routine starten)")
        v["on_missed"] = f["on_missed"]
    if "routine_id" in f:
        rid = f["routine_id"] or None
        if rid:
            r = await db.get(Routine, rid)
            if not r:
                raise Invalid("Routine nicht gefunden")
            if owner and r.agent_id != owner:
                raise Invalid("Keine Berechtigung: nur eigene Routinen verknüpfen")
        v["routine_id"] = rid
        if rid and "on_missed" not in v:
            v["on_missed"] = "routine"
    for b in ("show_on_home", "enabled"):
        if f.get(b) is not None:
            v[b] = bool(f[b])
    action = v.get("on_missed", old.on_missed if old else "none")
    routine = v["routine_id"] if "routine_id" in v else (old.routine_id if old else None)
    if action == "routine" and not routine:
        raise Invalid("Für „Routine starten“ eine Routine auswählen")
    slots = v.get("slots", old.slots if old else [])
    if action != "none" and not any(s.get("time") for s in slots):
        raise Invalid("Zum Nachhaken braucht mindestens ein Zeitfenster eine Uhrzeit")
    return v


async def create(db: AsyncSession, f: dict, created_by: str = USER, owner: str | None = None) -> Tracker:
    if await db.scalar(select(func.count()).select_from(Tracker)) >= MAX_TRACKERS:
        raise Invalid(f"Höchstens {MAX_TRACKERS} Tracker")
    v = await _clean(db, f, False, None, owner)
    pos = (await db.scalar(select(func.max(Tracker.position)))) or 0
    t = Tracker(**{"on_missed": "none", "fired": {}, "position": pos + 1, **v}, created_by=created_by)
    db.add(t)
    await db.flush()
    return t


async def get(db: AsyncSession, tracker_id: str) -> Tracker:
    t = await db.get(Tracker, tracker_id)
    if not t:
        raise NotFound("Tracker nicht gefunden")
    return t


async def update(db: AsyncSession, tracker_id: str, f: dict, owner: str | None = None) -> Tracker:
    t = await get(db, tracker_id)
    v = await _clean(db, f, True, t, owner)
    for k, val in v.items():
        setattr(t, k, val)
    if "slots" in v:  # removed slots: forget whether they were followed up
        t.fired = {k: d for k, d in (t.fired or {}).items() if any(s["key"] == k for s in v["slots"])}
    await db.flush()
    return t


async def remove(db: AsyncSession, tracker_id: str) -> None:
    await db.delete(await get(db, tracker_id))


async def listing(db: AsyncSession) -> list[Tracker]:
    return list((await db.scalars(select(Tracker).order_by(Tracker.position, Tracker.created_at))).all())


# ---------------- ticks ----------------
def parse_day(day, today: date | None = None) -> date:
    today = today or local_today()
    if day in (None, "", "heute", "today"):
        return today
    if day in ("gestern", "yesterday"):
        return today - timedelta(days=1)
    try:
        d = date.fromisoformat(str(day)[:10])
    except ValueError as e:
        raise Invalid(f"Tag „{day}“ ungültig – Format JJJJ-MM-TT") from e
    if d > today:
        raise Invalid("Für die Zukunft kann nichts abgehakt werden")
    if d < today - timedelta(days=MAX_BACK):
        raise Invalid(f"Höchstens {MAX_BACK} Tage zurück")
    return d


async def checks(db: AsyncSession, ids: list[str], since: date) -> dict[str, dict[str, dict[str, TrackerCheck]]]:
    """{tracker_id: {day: {slot: check}}} from `since` on."""
    out: dict = {i: {} for i in ids}
    if ids:
        for c in (await db.scalars(select(TrackerCheck).where(TrackerCheck.tracker_id.in_(ids), TrackerCheck.day >= since.isoformat()))).all():
            out[c.tracker_id].setdefault(c.day, {})[c.slot] = c
    return out


def _find_slot(t: Tracker, slot) -> dict:
    s = str(slot).strip().lower()
    for x in t.slots:
        if s in (x["key"], x["label"].lower(), (x.get("time") or "-")):
            return x
    raise Invalid(f"Zeitfenster „{slot}“ gibt es nicht – vorhanden: " + ", ".join(f"{x['key']} ({slot_text(x)})" for x in t.slots))


def _slot_at(s: dict, d: date) -> datetime:
    h, m = map(int, s["time"].split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=schedule.tz())


def pick_slot(t: Tracker, now: datetime, done) -> dict | None:
    """The slot a tick without slot means (link, „hab ich gemacht“); None = everything done.

    Timed slots: the latest open one that is due or due soon (time <= now + 2 h) – a late morning tick still
    counts for the morning; if none is due yet, the earliest open one (taken early).
    Slots without time („3× egal wann“): simply the next open one.
    """
    local = now.astimezone(schedule.tz())
    open_ = [s for s in t.slots if s["key"] not in done]
    timed = [s for s in open_ if s.get("time")]
    due = [s for s in timed if _slot_at(s, local.date()) <= local + SOON]
    if due:
        return max(due, key=lambda s: s["time"])
    untimed = [s for s in open_ if not s.get("time")]
    if untimed:
        return untimed[0]
    return min(timed, key=lambda s: s["time"]) if timed else None


async def check(db: AsyncSession, tracker_id: str, slot=None, day=None, by: str = USER, undo: bool = False,
                now: datetime | None = None, strict: bool = True) -> dict:
    """Tick (or untick) a slot; without slot the one that fits the time of day (undo: the last ticked one).

    status: "checked" | "unchecked" | "all_done" (nothing open – an error when strict)."""
    t = await get(db, tracker_id)
    now = now or utcnow()
    today = local_today(now)
    d = parse_day(day, today)
    done = (await checks(db, [t.id], d))[t.id].get(d.isoformat(), {})
    if slot:
        s = _find_slot(t, slot)
    elif undo:
        s = max((x for x in t.slots if x["key"] in done), key=lambda x: done[x["key"]].at, default=None)
        if not s:
            raise Invalid("Da ist nichts abgehakt")
    else:  # today: the slot that fits the time of day; an earlier day: the first open one
        s = pick_slot(t, now, done) if d == today else next((x for x in t.slots if x["key"] not in done), None)
        if not s:
            if strict:
                raise Invalid(f"„{t.name}“ ist für diesen Tag schon komplett abgehakt")
            return {"tracker": t, "slot": None, "day": d.isoformat(), "checked": False, "status": "all_done"}
    c = done.get(s["key"])
    if c and not undo and not strict:  # a fixed slot that is ticked already
        return {"tracker": t, "slot": s, "day": d.isoformat(), "checked": False, "status": "already"}
    if undo and c:
        await db.delete(c)
    elif not undo and not c:
        db.add(TrackerCheck(tracker_id=t.id, day=d.isoformat(), slot=s["key"], by=by))
    await db.flush()
    return {"tracker": t, "slot": s, "day": d.isoformat(), "checked": not undo, "status": "unchecked" if undo else "checked"}


def day_status(t: Tracker, d: date, done: dict[str, TrackerCheck]) -> dict:
    slots = [{**s, "done": s["key"] in done, "by": done[s["key"]].by if s["key"] in done else None,
              "at": done[s["key"]].at.isoformat() if s["key"] in done else None} for s in t.slots]
    return {"day": d.isoformat(), "due": active_on(t, d), "slots": slots, "done": sum(x["done"] for x in slots), "total": len(slots)}


def streak(t: Tracker, by_day: dict[str, dict], today: date) -> int:
    """Days in a row with everything ticked; days the tracker is not due skip, an open today does not break it."""
    n, d = 0, today
    created = t.created_at.replace(tzinfo=t.created_at.tzinfo or timezone.utc).astimezone(schedule.tz()).date() if t.created_at else today
    start = created - timedelta(days=MAX_BACK)
    keys = {s["key"] for s in t.slots}
    while d >= start:
        if active_on(t, d):
            if keys <= set(by_day.get(d.isoformat(), {})):
                n += 1
            elif d != today:
                break
        d -= timedelta(days=1)
    return n


async def overview(db: AsyncSession, trackers: list[Tracker], days: int = RECENT) -> dict[str, dict]:
    """Per tracker: today, the last `days` days (oldest first) and the streak."""
    today = local_today()
    data = await checks(db, [t.id for t in trackers], today - timedelta(days=max(days, 366)))
    out = {}
    for t in trackers:
        by_day = data[t.id]
        recent = [day_status(t, d, by_day.get(d.isoformat(), {})) for d in (today - timedelta(days=i) for i in range(days - 1, -1, -1))]
        nxt = pick_slot(t, utcnow(), by_day.get(today.isoformat(), {}))
        out[t.id] = {"today": recent[-1], "recent": recent, "streak": streak(t, by_day, today), "next": nxt["key"] if nxt else None}
    return out


# ---------------- follow-up when a slot stays open ----------------
def due_slots(t: Tracker, now: datetime, done: set[str]) -> list[dict]:
    """Slots to follow up now: due today, time reached (at most GRACE ago), not ticked, not followed up today."""
    local = now.astimezone(schedule.tz())
    d = local.date()
    if not t.enabled or t.on_missed == "none" or not active_on(t, d):
        return []
    out = []
    for s in t.slots:
        if not s.get("time") or s["key"] in done or (t.fired or {}).get(s["key"]) == d.isoformat():
            continue
        at = _slot_at(s, d)
        if at <= local <= at + GRACE:
            out.append(s)
    return out


async def schedule_tick(now: datetime | None = None) -> None:
    """Called by the scheduler every few seconds."""
    now = now or utcnow()
    today = local_today(now)
    due: list[tuple[str, str]] = []
    async with session_scope() as db:
        rows = (await db.scalars(select(Tracker).where(Tracker.enabled.is_(True), Tracker.on_missed != "none"))).all()
        if not rows:
            return
        data = await checks(db, [t.id for t in rows], today)
        for t in rows:
            slots = due_slots(t, now, set(data[t.id].get(today.isoformat(), {})))
            if slots:
                t.fired = {**(t.fired or {}), **{s["key"]: today.isoformat() for s in slots}}  # reassign: JSON is not change-tracked
                due += [(t.id, s["key"]) for s in slots]
    for tid, key in due:
        try:
            await missed(tid, key)
        except Exception:  # noqa: BLE001
            log.exception("tracker follow-up %s/%s failed", tid, key)


async def _notify(db: AsyncSession, t: Tracker, text: str) -> None:
    sender = t.created_by if t.created_by != USER and await db.get(Agent, t.created_by) else None
    if not sender:
        sender = await db.scalar(select(Agent.id).where(Agent.is_main.is_(True)))
    db.add(Message(from_id=sender or USER, to_id=USER, kind="notice", priority="high", title=f"☑ {t.name}"[:120], text=text[:4000]))


async def missed(tracker_id: str, key: str) -> dict:
    """Follow up an open slot now: notification or the linked routine (falls back to a notification)."""
    from . import routines

    async with session_scope() as db:
        t = await get(db, tracker_id)
        s = _find_slot(t, key)
        text = f"„{t.name}“ – {slot_text(s)} ist heute noch nicht abgehakt."
        if t.on_missed == "routine" and t.routine_id and await db.get(Routine, t.routine_id):
            context = (f"Gestartet vom Tracker: {text} Sagt der Nutzer, dass es erledigt ist, hake es mit "
                       f"tracker_check(id=\"{t.id}\", slot=\"{s['key']}\") ab.")
            try:
                routines.enqueue(t.routine_id, "tracker", context)
                return {"ok": True, "action": "routine", "text": text}
            except Invalid:
                log.info("tracker %s: routine %s is busy – notification instead", t.id, t.routine_id)
        if t.on_missed == "none":
            return {"ok": True, "action": "none", "text": text}
        await _notify(db, t, text + " Hak es in IDA unter Tracker ab, wenn es erledigt ist.")
        return {"ok": True, "action": "notify", "text": text}


async def linked(db: AsyncSession, routine_id: str) -> list[Tracker]:
    return list((await db.scalars(select(Tracker).where(Tracker.routine_id == routine_id))).all())


# ---------------- tap links (NFC tags): opening the link ticks the tracker ----------------
async def links(db: AsyncSession, tracker_id: str) -> list[TrackerLink]:
    return list((await db.scalars(select(TrackerLink).where(TrackerLink.tracker_id == tracker_id).order_by(TrackerLink.created_at))).all())


async def link_counts(db: AsyncSession) -> dict[str, int]:
    return dict((await db.execute(select(TrackerLink.tracker_id, func.count()).group_by(TrackerLink.tracker_id))).all())


async def create_link(db: AsyncSession, tracker_id: str, f: dict) -> TrackerLink:
    t = await get(db, tracker_id)
    if len(await links(db, t.id)) >= MAX_LINKS:
        raise Invalid(f"Höchstens {MAX_LINKS} Links pro Tracker")
    name = str(f.get("name") or "").strip() or "NFC-Tag"
    if len(name) > 40:
        raise Invalid("Name: höchstens 40 Zeichen")
    slot = _find_slot(t, f["slot"])["key"] if f.get("slot") else None
    link = TrackerLink(tracker_id=t.id, name=name, slot=slot, token=secrets.token_urlsafe(16))
    db.add(link)
    await db.flush()
    return link


async def delete_link(db: AsyncSession, tracker_id: str, link_id: str) -> None:
    link = await db.get(TrackerLink, link_id)
    if not link or link.tracker_id != tracker_id:
        raise NotFound("Link nicht gefunden")
    await db.delete(link)


async def tap(db: AsyncSession, token: str, now: datetime | None = None) -> dict:
    """A link was opened: tick the slot that fits the time (or the link's fixed one) – once, even if the tag is read twice."""
    now = now or utcnow()
    link = await db.scalar(select(TrackerLink).where(TrackerLink.token == token))
    if not link:
        raise NotFound("Diesen Link gibt es nicht mehr")
    t = await get(db, link.tracker_id)
    last = link.last_used_at.replace(tzinfo=link.last_used_at.tzinfo or timezone.utc) if link.last_used_at else None
    if last and now - last < REPEAT:
        res = {"tracker": t, "slot": None, "day": local_today(now).isoformat(), "checked": False, "status": "repeat"}
    else:
        slot = link.slot if link.slot and any(s["key"] == link.slot for s in t.slots) else None
        res = await check(db, t.id, slot, by=LINK_BY, now=now, strict=False)
        if res["status"] == "checked":
            link.uses += 1
        link.last_used_at = now
    await db.flush()
    return {**res, "link": link}


async def link_tracker(db: AsyncSession, token: str) -> Tracker:
    link = await db.scalar(select(TrackerLink).where(TrackerLink.token == token))
    if not link:
        raise NotFound("Diesen Link gibt es nicht mehr")
    return await get(db, link.tracker_id)
