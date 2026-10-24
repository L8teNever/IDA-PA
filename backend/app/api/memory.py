from fastapi import APIRouter
from sqlalchemy import func, select

from ..models import Agent, MemoryEntry
from ..schemas import MemoryAdd, MemoryUpdate, Scope
from ..services.claude.prompts import labels as get_labels
from ..services.errors import Invalid
from ..services.memory import embeddings
from ..services.memory import store
from ..services.memory.consolidate import consolidate
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


async def _valid(db, scope: str) -> None:
    if scope != store.SHARED and not await db.get(Agent, scope):
        raise Invalid("Unbekannter Gedächtnis-Bereich")


@router.get("/scopes")
async def scopes(db: DB):
    agents = (await db.scalars(select(Agent).order_by(Agent.position))).all()
    out = [{"id": store.SHARED, "label": "Gemeinsames Gedächtnis", "count": await store.count(db, store.SHARED),
            "enabled": True, "backup": await store.has_backup(db, store.SHARED)}]
    for a in agents:
        out.append({"id": a.id, "label": a.name, "palette": a.palette, "count": await store.count(db, a.id),
                    "enabled": a.memory_enabled, "backup": await store.has_backup(db, a.id)})
    return out


@router.get("/status")
async def status(db: DB):
    m = await embeddings.model()
    total = await db.scalar(select(func.count()).select_from(MemoryEntry)) or 0
    done = await db.scalar(select(func.count()).select_from(MemoryEntry).where(MemoryEntry.embed_model == m)) if m else 0
    return {"engine": "Datenbank + BM25", "vectors": m, "embedded": done or 0, "total": total}


@router.get("")
async def list_entries(db: DB, scope: str = store.SHARED, q: str = ""):
    await _valid(db, scope)
    labels = await get_labels(db)
    if q.strip():
        items = await store.entries(db, scope)
        rel = await store.relevances(items, q)
        ranked = sorted((x for x in store.rank(items, rel) if x[1] > 0), key=lambda x: -x[2])
        items = [x[0] for x in ranked]
    else:
        items = sorted(await store.entries(db, scope), key=lambda e: (not e.pinned, -e.updated_at.timestamp()))
    return [ser.memory_entry(e, labels) for e in items]


@router.post("", status_code=201)
async def add(body: MemoryAdd, db: DB):
    await _valid(db, body.scope)
    return await store.add(db, body.scope, body.model_dump(exclude_unset=True, exclude={"scope"}), "user")


@router.patch("/{entry_id}")
async def update(entry_id: str, body: MemoryUpdate, db: DB):
    return await store.update(db, body.scope, entry_id, body.model_dump(exclude_unset=True, exclude={"scope"}), "user")


@router.delete("/{entry_id}")
async def delete(entry_id: str, scope: str, db: DB):
    return await store.remove(db, scope, entry_id)


@router.post("/consolidate")
async def run_consolidate(body: Scope, db: DB):
    await _valid(db, body.scope)
    return await consolidate(body.scope)


@router.post("/undo")
async def undo(body: Scope, db: DB):
    return await store.undo(db, body.scope)
