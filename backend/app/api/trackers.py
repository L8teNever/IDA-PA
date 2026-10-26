"""Trackers: daily things to tick off („Tablette morgens + abends“), with a follow-up when a slot stays open."""
from fastapi import APIRouter

from ..schemas import TrackerCreate, TrackerLinkCreate, TrackerTick, TrackerUpdate
from ..services import trackers as svc
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/trackers", tags=["trackers"])


async def _view(db, t) -> dict:
    return ser.tracker(t, (await svc.overview(db, [t]))[t.id])


@router.get("")
async def list_trackers(db: DB):
    rows = await svc.listing(db)
    status, counts = await svc.overview(db, rows), await svc.link_counts(db)
    return [{**ser.tracker(t, status[t.id]), "links": counts.get(t.id, 0)} for t in rows]


@router.post("", status_code=201)
async def create(body: TrackerCreate, db: DB):
    return await _view(db, await svc.create(db, body.model_dump()))


@router.patch("/{tracker_id}")
async def update(tracker_id: str, body: TrackerUpdate, db: DB):
    return await _view(db, await svc.update(db, tracker_id, body.model_dump(exclude_unset=True)))


@router.delete("/{tracker_id}")
async def delete(tracker_id: str, db: DB):
    await svc.remove(db, tracker_id)
    return {"ok": True}


@router.post("/{tracker_id}/check")
async def check(tracker_id: str, body: TrackerTick, db: DB):
    """Tick or untick a slot – today or a forgotten earlier day."""
    res = await svc.check(db, tracker_id, body.slot, body.day, undo=body.undo)
    return await _view(db, res["tracker"])


@router.get("/{tracker_id}/history")
async def history(tracker_id: str, db: DB, days: int = 60):
    t = await svc.get(db, tracker_id)
    return (await svc.overview(db, [t], max(1, min(days, 366))))[t.id]


@router.post("/{tracker_id}/missed/{slot}")
async def missed_now(tracker_id: str, slot: str, db: DB):
    """Test the follow-up now (as if the slot were still open at its time)."""
    await svc.get(db, tracker_id)
    await db.commit()
    return await svc.missed(tracker_id, slot)


@router.get("/{tracker_id}/links")
async def list_links(tracker_id: str, db: DB):
    await svc.get(db, tracker_id)
    return [ser.tracker_link(x) for x in await svc.links(db, tracker_id)]


@router.post("/{tracker_id}/links", status_code=201)
async def create_link(tracker_id: str, body: TrackerLinkCreate, db: DB):
    """A secret URL /t/<token> – e.g. on an NFC tag; opening it ticks the tracker."""
    return ser.tracker_link(await svc.create_link(db, tracker_id, body.model_dump()))


@router.delete("/{tracker_id}/links/{link_id}")
async def delete_link(tracker_id: str, link_id: str, db: DB):
    await svc.delete_link(db, tracker_id, link_id)
    return {"ok": True}


tap_router = APIRouter(prefix="/api/v1/tap", tags=["trackers"])


@tap_router.post("/{token}")
async def tap(token: str, db: DB):
    """Opened link (NFC tag): ticks the slot that fits the time of day – the page /t/<token> calls this once."""
    res = await svc.tap(db, token)
    t = res["tracker"]
    return {"status": res["status"], "day": res["day"], "slot": res["slot"], "link": res["link"].name,
            "tracker": ser.tracker(t, (await svc.overview(db, [t]))[t.id])}


@tap_router.get("/{token}")
async def tap_status(token: str, db: DB):
    """The link's tracker without ticking (the page after a reload)."""
    t = await svc.link_tracker(db, token)
    return {"status": "shown", "tracker": ser.tracker(t, (await svc.overview(db, [t]))[t.id])}
