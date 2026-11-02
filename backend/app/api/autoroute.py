"""Einstellungen → Auto: which models "Auto" may use and whether it may change provider."""
from fastapi import APIRouter

from ..schemas import AutoRoute, RouteRating
from ..services import autoroute, providers
from .deps import DB

router = APIRouter(tags=["autoroute"])


async def _out(db) -> dict:
    cfg = await autoroute.config(db)
    profiles = await autoroute.available(db)
    defaults = autoroute.default_allowed(profiles)
    return {**cfg, "stats": await autoroute.stats(db), "allowed": cfg["models"] if cfg["models"] is not None else defaults,
            "available": [{"id": p.id, "name": p.name, "provider": p.provider, "provider_name": providers.PROVIDERS[p.provider],
                           "tier": p.tier, "level": p.level} for p in profiles]}


@router.get("/api/v1/autoroute")
async def get_autoroute(db: DB):
    return await _out(db)


@router.put("/api/v1/autoroute")
async def put_autoroute(body: AutoRoute, db: DB):
    await autoroute.save(db, body.provider_mode, body.models)
    return await _out(db)


@router.post("/api/v1/autoroute/rate")
async def rate(body: RouteRating, db: DB):
    """Rating under an Auto answer: ok (passte) | weak (zu schwach) | strong (zu stark)."""
    return {"route": await autoroute.rate(db, body.message_id, body.rating, body.better_model)}


@router.delete("/api/v1/autoroute/learned")
async def reset(db: DB):
    """Forgets all ratings – Auto decides only by its rules again."""
    await autoroute.reset_learning(db)
    return await _out(db)
