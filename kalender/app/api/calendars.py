"""Kalender verwalten (Privat, Schule, …)."""
from fastapi import APIRouter, Body, Depends
from sqlalchemy import func, select

from .. import palette
from ..auth import Caller, caller
from ..db import session_scope
from ..errors import Forbidden, Invalid, NotFound
from ..models import Calendar, Event
from ..services import search, store
from ..services import settings as cfg

router = APIRouter(prefix="/api/v1", tags=["calendars"])
ACCESS = ("write", "read", "none")


def view(c: Calendar) -> dict:
    return {"id": c.id, "name": c.name, "color": c.color, "kind": c.kind, "visible": c.visible, "default_alarms": c.default_alarms or [],
            "agent_access": c.agent_access, "position": c.position, "is_default": c.is_default, "read_only": c.kind == "holidays"}


def _check(body: dict, c: Caller, creating: bool) -> dict:
    out = {}
    if "name" in body:
        n = str(body["name"] or "").strip()
        if not n:
            raise Invalid("Name fehlt")
        out["name"] = n[:80]
    if "color" in body:
        if body["color"] not in palette.KEYS:
            raise Invalid(f"Unbekannte Farbe ({', '.join(palette.KEYS)})")
        out["color"] = body["color"]
    if "visible" in body:
        out["visible"] = bool(body["visible"])
    if "default_alarms" in body:
        out["default_alarms"] = store._clean_alarms(body["default_alarms"])  # noqa: SLF001
    if "agent_access" in body:
        if c.by != "user":
            raise Forbidden("Was Agents dürfen, stellt nur der Nutzer ein")
        if body["agent_access"] not in ACCESS:
            raise Invalid("agent_access: write, read oder none")
        out["agent_access"] = body["agent_access"]
    if "position" in body:
        out["position"] = int(body["position"])
    if "kind" in body and creating:
        if body["kind"] not in ("normal", "birthdays"):
            raise Invalid("kind: normal oder birthdays")
        out["kind"] = body["kind"]
    return out


async def payload(db, by: str) -> list[dict]:
    rows = (await db.scalars(select(Calendar).order_by(Calendar.position, Calendar.name))).all()
    counts = dict((await db.execute(select(Event.calendar_id, func.count()).where(Event.deleted_at.is_(None)).group_by(Event.calendar_id))).all())
    return [{**view(r), "events": counts.get(r.id, 0)} for r in rows if store.readable(r, by)]


@router.get("/calendars")
async def list_calendars(c: Caller = Depends(caller)):
    async with session_scope() as db:
        return {"calendars": await payload(db, c.by)}


@router.post("/calendars")
async def create_calendar(body: dict = Body(...), c: Caller = Depends(caller)):
    f = _check(body, c, True)
    if not f.get("name"):
        raise Invalid("Name fehlt")
    async with session_scope(write=True) as db:
        n = await db.scalar(select(func.max(Calendar.position))) or 0
        cal = Calendar(**{"color": "blau", "position": min(n + 1, 8), "agent_access": "write", "default_alarms": [], **f})
        db.add(cal)
        await db.flush()
        await cfg.touch(db, "calendars")
        return view(cal)


@router.patch("/calendars/{cid}")
async def update_calendar(cid: str, body: dict = Body(...), c: Caller = Depends(caller)):
    f = _check(body, c, False)
    async with session_scope(write=True) as db:
        cal = await store.calendar_for(db, cid, c.by)
        if cal.kind == "holidays" and any(k not in ("visible", "color", "name") for k in f):
            raise Forbidden("Der Feiertags-Kalender lässt sich nur ein- und ausblenden oder umfärben")
        if c.by != "user" and not store.writable(cal, c.by):
            raise Forbidden(f"Der Kalender „{cal.name}“ ist für Agents nur lesbar")
        for k, v in f.items():
            setattr(cal, k, v)
        if body.get("is_default") is True and cal.kind == "normal":
            for o in (await db.scalars(select(Calendar).where(Calendar.is_default.is_(True)))).all():
                o.is_default = False
            cal.is_default = True
        await db.flush()
        await cfg.touch(db, "calendars")
        return view(cal)


@router.delete("/calendars/{cid}")
async def delete_calendar(cid: str, c: Caller = Depends(caller)):
    if c.by != "user":
        raise Forbidden("Kalender löschen darf nur der Nutzer")
    async with session_scope(write=True) as db:
        cal = await db.get(Calendar, cid)
        if not cal:
            raise NotFound("Kalender nicht gefunden")
        if cal.kind == "holidays":
            raise Forbidden("Der Feiertags-Kalender lässt sich nicht löschen (nur ausblenden)")
        if cal.is_default:
            raise Invalid("Der Standardkalender lässt sich nicht löschen – erst einen anderen zum Standard machen")
        for eid in (await db.scalars(select(Event.id).where(Event.calendar_id == cid))).all():
            await search.unindex(db, eid)
        n = await db.scalar(select(func.count()).select_from(Event).where(Event.calendar_id == cid))
        await db.delete(cal)
        await cfg.touch(db, "calendars")
        return {"ok": True, "deleted_events": n}
