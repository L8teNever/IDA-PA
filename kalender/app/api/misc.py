"""Verlauf, freie Zeit, Suche, Schnelleingabe, Einstellungen, Live-Stream, Backups, Meta."""
import asyncio
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select

from .. import palette
from ..auth import Caller, caller, last_change_id
from ..config import get_settings
from ..db import session_scope
from ..errors import Forbidden, Invalid, NotFound
from ..models import Calendar, Event
from ..services import backup, bus, parse, query, search, settings as cfg, store
from ..services import recurrence as rec
from ..services.timeutil import UTC, iso, local_now, parse_input, range_from, zone

router = APIRouter(prefix="/api/v1", tags=["misc"])


# ---------------- Verlauf / Rückgängig ----------------
@router.get("/changes")
async def list_changes(by: str | None = None, since: int | None = None, limit: int = 50, event_id: str | None = None, c: Caller = Depends(caller)):
    async with session_scope() as db:
        rows = await store.changes(db, by if c.by == "user" else c.by, since, limit, event_id)
        cals = {x.id: x for x in (await db.scalars(select(Calendar))).all()}
        return {"changes": [store.change_view(r) for r in rows if r.calendar_id in cals and store.readable(cals[r.calendar_id], c.by)]}


@router.post("/changes/{change_id}/undo")
async def undo_change(change_id: int, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return {"ok": True, **await store.undo(db, change_id, c.by, c.via)}


@router.post("/undo")
async def undo_last(body: dict = Body(default={}), c: Caller = Depends(caller)):
    """Die letzte (eigene) Änderung rückgängig machen; mit change_id eine bestimmte."""
    async with session_scope(write=True) as db:
        cid = body.get("change_id")
        if not cid:
            last = await store.last_change(db, c.by)
            if not last:
                raise NotFound("Es gibt nichts rückgängig zu machen")
            cid = last.id
        return {"ok": True, **await store.undo(db, int(cid), c.by, c.via)}


# ---------------- Freie Zeit, Konflikte, Übersicht, Statistik ----------------
@router.get("/freebusy")
async def freebusy(frm: str | None = Query(None, alias="from"), to: str | None = None, duration: int = 60, within: str | None = None,
                   days: str | None = None, c: Caller = Depends(caller)):
    a, b = range_from(frm or "heute", to)
    d = [int(x) for x in days.split(",") if x != ""] if days else None
    async with session_scope() as db:
        return {"slots": await query.free_slots(db, a, b, duration, within, d, c.by)}


@router.get("/conflicts")
async def conflicts(start: str, end: str, exclude: str | None = None, c: Caller = Depends(caller)):
    s, _ = parse_input(start)
    e, _ = parse_input(end)
    if e <= s:
        raise Invalid("Das Ende liegt nicht nach dem Anfang")
    async with session_scope() as db:
        items = await query.conflicts(db, s, e, rec.split_id(exclude)[0] if exclude else None, c.by)
        return {"conflicts": items, "free": not items}


@router.get("/brief")
async def brief(date: str | None = None, c: Caller = Depends(caller)):
    a, _ = range_from(date or "heute", None)
    async with session_scope() as db:
        return await query.day_brief(db, a.astimezone(zone()).date(), c.by)


@router.get("/stats")
async def stats(frm: str | None = Query(None, alias="from"), to: str | None = None, group_by: str = "calendar", c: Caller = Depends(caller)):
    a, b = range_from(frm or "diese woche", to)
    async with session_scope() as db:
        return await query.stats(db, a, b, group_by, c.by)


# ---------------- Suche ----------------
@router.get("/search")
async def do_search(q: str = "", calendars: str | None = None, frm: str | None = Query(None, alias="from"), to: str | None = None,
                    flags: str | None = None, limit: int = 60, c: Caller = Depends(caller)):
    """Sucht in Titel, Ort, Beschreibung, Kalendername. Zeitangaben im Text („nächste woche“) werden zum Zeitraum-Filter."""
    now = datetime.now(UTC)
    rest, rng, label = (q, None, "")
    if frm or to:
        rng = range_from(frm, to)
        label = "Zeitraum"
    elif q:
        rest, rng, label = parse.extract_range(q)
    want = set((flags or "").split(",")) - {""}
    cal_ids = set(calendars.split(",")) if calendars else None
    async with session_scope() as db:
        ids = await search.matching_ids(db, rest) if rest.strip() else None
        if ids is None and rng is None:
            return {"query": rest, "range": None, "upcoming": [], "past": [], "total": 0}
        cals = {x.id: x for x in (await db.scalars(select(Calendar))).all()}
        if rng:
            items = [(ev, occ, cal) for ev, occ, cal in await query.raw_occurrences(db, rng[0], rng[1], c.by, cal_ids)
                     if ids is None or ev.id in ids]
            upcoming = [query.occ_view(ev, occ, cal) | {"more": 0} for ev, occ, cal in items if _ok(ev, want)][:limit]
            return {"query": rest, "range": {"from": iso(rng[0]), "to": iso(rng[1]), "label": label}, "upcoming": upcoming, "past": [], "total": len(upcoming)}
        up, past = [], []
        for eid in ids:
            ev = await db.get(Event, eid)
            if not ev or ev.deleted_at or ev.calendar_id not in cals or not store.readable(cals[ev.calendar_id], c.by):
                continue
            if cal_ids and ev.calendar_id not in cal_ids or not _ok(ev, want):
                continue
            occ, more = await query.next_after(db, ev, now)
            if occ:
                up.append(query.occ_view(ev, occ, cals[ev.calendar_id]) | {"more": more})
            else:
                last = ev.series_end or ev.end_at
                occs = rec.expand(ev, last - (ev.end_at - ev.start_at) - timedelta(days=1), last + timedelta(seconds=1)) if ev.rrule else \
                    [rec.Occ(rec.key_of(rec.local_start(ev.start_at, ev.tzid)), ev.start_at, ev.end_at)]
                if occs:
                    past.append(query.occ_view(ev, occs[-1], cals[ev.calendar_id]) | {"more": 0})
        up.sort(key=lambda v: v["start"])
        past.sort(key=lambda v: v["start"], reverse=True)
        return {"query": rest, "range": None, "upcoming": up[:limit], "past": past[:limit], "total": len(up) + len(past)}


def _ok(ev: Event, want: set[str]) -> bool:
    if "recurring" in want and not (ev.rrule or ev.recurrence_parent_id):
        return False
    if "alarm" in want and not ev.alarms:
        return False
    if "by_agent" in want and ev.created_by == "user" and ev.updated_by == "user":
        return False
    if "all_day" in want and not ev.all_day:
        return False
    if "tentative" in want and ev.status != "tentative":
        return False
    return True


@router.get("/suggest")
async def suggest(prefix: str, c: Caller = Depends(caller)):
    async with session_scope() as db:
        return {"suggestions": await search.suggest_titles(db, prefix)}


@router.post("/quick")
async def quick(body: dict = Body(...), c: Caller = Depends(caller)):
    """Schnelleingabe → Vorschlag (noch nicht gespeichert)."""
    text = str(body.get("text") or "").strip()
    if not text:
        raise Invalid("Text fehlt")
    async with session_scope() as db:
        names = [x.name for x in (await db.scalars(select(Calendar).where(Calendar.kind == "normal"))).all() if store.readable(x, c.by)]
        s = await cfg.get_all(db)
    res = parse.quick(text, calendars=names, default_min=int(s.get("default_duration_min") or 60))
    if not res["title"]:
        res["title"] = ""
    return res


# ---------------- Einstellungen ----------------
@router.get("/settings")
async def get_settings_(c: Caller = Depends(caller)):
    async with session_scope() as db:
        return await cfg.get_all(db)


@router.put("/settings")
async def put_settings(body: dict = Body(...), c: Caller = Depends(caller)):
    if c.by != "user":
        raise Forbidden("Einstellungen ändert nur der Nutzer")
    async with session_scope(write=True) as db:
        return await cfg.update(db, body)


# ---------------- Live-Stream ----------------
@router.get("/stream")
async def stream(request: Request, c: Caller = Depends(caller)):
    """Server-Sent Events: changed (Termine geändert), alarm (Erinnerung fällig). Neu verbunden → verpasste Änderungen nachliefern."""
    q = bus.subscribe()
    last = request.headers.get("last-event-id", "")

    async def gen():
        try:
            yield "retry: 3000\n\n"
            if last.isdigit():
                async with session_scope() as db:
                    missed = await store.changes(db, None, int(last), 200)
                if missed:
                    ids = sorted({m.event_id for m in missed})
                    yield f"id: {max(m.id for m in missed)}\ndata: {json.dumps({'type': 'changed', 'ids': ids, 'by': 'sync', 'catchup': True})}\n\n"
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), 20)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                head = f"id: {evt['change_id']}\n" if evt.get("change_id") else ""
                yield f"{head}data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


# ---------------- Backups ----------------
@router.get("/backups")
async def backups(c: Caller = Depends(caller)):
    return {"backups": backup.listing(), "keep": get_settings().backup_keep}


@router.post("/backups")
async def backup_now(c: Caller = Depends(caller)):
    if c.by != "user":
        raise Forbidden("Backups startet nur der Nutzer")
    return await backup.run()


@router.get("/backups/{name}")
async def backup_file(name: str, c: Caller = Depends(caller)):
    if c.by != "user":
        raise Forbidden("Backups lädt nur der Nutzer herunter")
    return FileResponse(backup.path(name), filename=name)


# ---------------- Meta ----------------
@router.get("/health")
async def health():
    return {"ok": True, "time": local_now().isoformat(timespec="seconds")}


@router.get("/meta")
async def meta(request: Request):
    s = get_settings()
    return {"name": "IDA Kalender", "version": getattr(request.app.state, "version", ""), "timezone": s.timezone, "palette": palette.palette(),
            "ida": bool(s.ida_webhook_url), "public_url": s.kalender_public_url, "mcp": bool(s.kalender_mcp_token),
            "state": s.kalender_state}
