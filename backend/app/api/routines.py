from fastapi import APIRouter
from sqlalchemy import select

from ..models import Routine, RoutineRun
from ..schemas import RoutineCreate, RoutineUpdate, TriggerTest
from ..services import routines as svc
from ..services import trigger_script, triggers
from ..services.errors import Invalid, NotFound
from ..services.mcp.client import McpError
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/routines", tags=["routines"])


async def _view(db, r: Routine) -> dict:
    last = await db.scalar(select(RoutineRun).where(RoutineRun.routine_id == r.id).order_by(RoutineRun.at.desc()).limit(1))
    return ser.routine(r, last, svc.is_running(r.id))


@router.get("")
async def list_routines(db: DB):
    return [await _view(db, r) for r in (await db.scalars(select(Routine).order_by(Routine.created_at))).all()]


@router.get("/{routine_id}")
async def get_routine(routine_id: str, db: DB):
    r = await db.get(Routine, routine_id)
    if not r:
        raise NotFound("Routine nicht gefunden")
    runs = (await db.scalars(select(RoutineRun).where(RoutineRun.routine_id == r.id).order_by(RoutineRun.at.desc()))).all()
    return {**await _view(db, r), "runs": [ser.routine_run(x) for x in runs]}


@router.post("", status_code=201)
async def create(body: RoutineCreate, db: DB):
    data = body.model_dump(exclude_unset=True)
    r = await svc.create(db, data, allow_trigger=True)  # the user may set triggers
    return await _view(db, r)


@router.patch("/{routine_id}")
async def update(routine_id: str, body: RoutineUpdate, db: DB):
    r = await svc.update(db, routine_id, body.model_dump(exclude_unset=True), allow_trigger=True)
    return await _view(db, r)


@router.delete("/{routine_id}")
async def delete(routine_id: str, db: DB):
    await svc.remove(db, routine_id)
    return {"ok": True}


@router.post("/{routine_id}/run")
async def run_now(routine_id: str, db: DB):
    if not await db.get(Routine, routine_id):
        raise NotFound("Routine nicht gefunden")
    svc.enqueue(routine_id, "manual")
    return {"queued": True}


@router.post("/{routine_id}/check")
async def check(routine_id: str):
    return await svc.check(routine_id)


@router.post("/test-trigger")
async def test_trigger(body: TriggerTest, db: DB):
    """Runs a trigger's tool once (nothing is stored, no routine starts)."""
    t = await svc.validate_trigger(db, body.trigger.model_dump())
    if not t:
        raise Invalid("Kein Auslöser angegeben")
    if t["type"] == "script":  # run once: shows the log and whether it would start the routine (it does not)
        res = await trigger_script.run(t["code"], {"_seen": []})  # test: everything counts as new
        return {"script": True, "fire": res["fire"], "info": res["info"], "log": res["log"], "error": res["error"]}
    try:
        text = await svc.run_trigger_tool(db, t)
    except McpError as e:
        raise Invalid(str(e)) from e
    except Exception as e:  # noqa: BLE001 – network errors
        raise Invalid(f"Tool nicht erreichbar: {e}") from e
    return {"preview": text[:800], "items": len(triggers.extract(text))}
