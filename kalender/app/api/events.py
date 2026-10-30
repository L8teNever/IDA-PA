"""Termine: lesen, anlegen, ändern, löschen, Papierkorb."""
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import select

from ..auth import Caller, caller, last_change_id
from ..db import session_scope
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..models import Calendar, Event
from ..services import holidays as hol
from ..services import idem, query, search, store
from ..services import recurrence as rec
from ..services import settings as cfg
from ..config import get_settings
from ..services.timeutil import parse_input, range_from

router = APIRouter(prefix="/api/v1", tags=["events"])


async def _detail(db, ev: Event, occ_key: str | None = None) -> dict:
    cal = await db.get(Calendar, ev.calendar_id)
    occ = None
    if occ_key and ev.rrule:
        s, e = store._occ_span(ev, occ_key)  # noqa: SLF001
        occ = rec.Occ(occ_key, s, e)
    return query.detail_view(ev, cal, occ)


async def _warn(db, ev: Event, by: str) -> list[dict]:
    """Überschneidungen des (ersten) Vorkommens – nur als Hinweis."""
    if ev.all_day or ev.transparency != "busy" or ev.status == "cancelled":
        return []
    return [{"id": v["id"], "title": v["title"], "start": v["start"], "end": v["end"]}
            for v in await query.conflicts(db, ev.start_at, ev.end_at, exclude_event_id=ev.id, by=by)][:5]


@router.get("/events")
async def list_events(frm: str | None = Query(None, alias="from"), to: str | None = None, calendars: str | None = None, q: str | None = None,
                      c: Caller = Depends(caller)):
    a, b = range_from(frm, to)
    ids = set(calendars.split(",")) if calendars else None
    async with session_scope() as db:
        items = await query.occurrences(db, a, b, c.by, ids)
        if q:
            hit = set(await search.matching_ids(db, q))
            items = [v for v in items if v["event_id"] in hit]
    return {"events": items, "from": a.isoformat(), "to": b.isoformat()}


@router.get("/events/{occ_id}")
async def get_event(occ_id: str, c: Caller = Depends(caller)):
    async with session_scope() as db:
        if occ_id.startswith("hol_"):
            cal = await db.scalar(select(Calendar).where(Calendar.kind == "holidays"))
            try:
                d = parse_input(f"{occ_id[4:8]}-{occ_id[8:10]}-{occ_id[10:12]}")[0]
            except Invalid as e:
                raise NotFound("Feiertag nicht gefunden") from e
            state = (await cfg.get_all(db)).get("state") or get_settings().kalender_state
            got = hol.occurrences(d, d + timedelta(days=1), state, cal) if cal else []
            if not got:
                raise NotFound("Feiertag nicht gefunden")
            return {"event": got[0]}
        eid, key = rec.split_id(occ_id)
        ev = await store.get_event(db, eid, c.by)
        return {"event": await _detail(db, ev, key)}


@router.post("/events")
async def create_event(request: Request, body: dict = Body(...), c: Caller = Depends(caller)):
    """Gibt es schon einen Termin mit dieser `uid` (die App vergibt sie beim Offline-Anlegen), wird er nicht noch einmal angelegt:
    die Antwort ist dann derselbe Termin mit `existing: true` (und `deleted: true`, wenn er inzwischen im Papierkorb liegt)."""
    async with session_scope(write=True) as db:
        async def go():
            ev = await store.create_event(db, body, c.by, c.via)
            existing = bool(getattr(ev, "_existing", False))
            out = {"event": await _detail(db, ev), "change_id": None if existing else last_change_id(db),
                   "conflicts": [] if existing else await _warn(db, ev, c.by)}
            if existing:
                out.update({"existing": True, "deleted": ev.deleted_at is not None})
            return out
        return await idem.run(db, request, go)


@router.patch("/events/{occ_id}")
async def update_event(occ_id: str, request: Request, body: dict = Body(...), scope: str = "all", c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        async def go():
            eid, key = rec.split_id(occ_id)
            if body.get("sequence") is not None:  # Offline-Änderungen: nur, wenn der Termin seitdem nicht geändert wurde
                cur = await store.get_event(db, eid, c.by, write=True)
                if int(body["sequence"]) != (cur.sequence or 0):
                    raise Conflict("Der Termin wurde zwischenzeitlich geändert", {"server": await _detail(db, cur)})
            evs = await store.update_occurrence(db, occ_id, {k: v for k, v in body.items() if k != "sequence"}, scope, c.by, c.via)
            main = evs[0]
            return {"event": await _detail(db, main, rec.key_of(rec.local_start(main.start_at, main.tzid)) if main.rrule else None),
                    "change_id": last_change_id(db), "conflicts": await _warn(db, main, c.by)}
        return await idem.run(db, request, go)


@router.delete("/events/{occ_id}")
async def delete_event(occ_id: str, request: Request, scope: str = "all", sequence: int | None = None, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        async def go():
            if sequence is not None:
                cur = await store.get_event(db, rec.split_id(occ_id)[0], c.by, write=True)
                if sequence != (cur.sequence or 0):
                    raise Conflict("Der Termin wurde zwischenzeitlich geändert", {"server": await _detail(db, cur)})
            evs = await store.delete_occurrence(db, occ_id, scope, c.by, c.via)
            return {"ok": True, "title": evs[0].title, "change_id": last_change_id(db)}
        return await idem.run(db, request, go)


@router.post("/events/{event_id}/restore")
async def restore_event(event_id: str, request: Request, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        async def go():
            ev = await store.restore(db, event_id, c.by, c.via)
            return {"event": await _detail(db, ev), "change_id": last_change_id(db)}
        return await idem.run(db, request, go)


@router.get("/trash")
async def trash(c: Caller = Depends(caller)):
    async with session_scope() as db:
        cals = {x.id: x for x in (await db.scalars(select(Calendar))).all()}
        return {"events": [{**query.detail_view(e, cals[e.calendar_id]), "deleted_at": e.deleted_at.isoformat()} for e in await store.trash(db, c.by)],
                "keep_days": get_settings().trash_days}


@router.delete("/trash")
async def empty_trash(c: Caller = Depends(caller)):
    if c.by != "user":
        raise Forbidden("Den Papierkorb leert nur der Nutzer")
    async with session_scope(write=True) as db:
        return {"ok": True, "removed": await store.purge_trash(db)}
