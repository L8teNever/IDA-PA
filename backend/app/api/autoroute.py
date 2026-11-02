"""Einstellungen → Auto: which models "Auto" may use and whether it may change provider."""
from fastapi import APIRouter

from ..schemas import AutoRoute
from ..services import autoroute, providers
from .deps import DB

router = APIRouter(tags=["autoroute"])


async def _out(db) -> dict:
    cfg = await autoroute.config(db)
    profiles = await autoroute.available(db)
    defaults = autoroute.default_allowed(profiles)
    return {**cfg, "allowed": cfg["models"] if cfg["models"] is not None else defaults,
            "available": [{"id": p.id, "name": p.name, "provider": p.provider, "provider_name": providers.PROVIDERS[p.provider],
                           "tier": p.tier, "level": p.level} for p in profiles]}


@router.get("/api/v1/autoroute")
async def get_autoroute(db: DB):
    return await _out(db)


@router.put("/api/v1/autoroute")
async def put_autoroute(body: AutoRoute, db: DB):
    await autoroute.save(db, body.provider_mode, body.models)
    return await _out(db)
