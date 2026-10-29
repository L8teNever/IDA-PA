"""ICS: Import, Export, Abo-Link."""
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response

from ..auth import Caller, caller
from ..config import get_settings
from ..db import session_scope
from ..errors import Forbidden, Invalid, NotFound
from ..services import ics
from ..services import settings as cfg

router = APIRouter(tags=["ics"])
MAX_ICS = 10 * 1024 * 1024


@router.post("/api/v1/import")
async def import_ics(file: UploadFile = File(...), calendar: str | None = Form(None), c: Caller = Depends(caller)):
    data = await file.read(MAX_ICS + 1)
    if len(data) > MAX_ICS:
        raise Invalid("Die Datei ist zu groß (höchstens 10 MB)")
    async with session_scope(write=True) as db:
        return {"ok": True, **await ics.import_(db, data, calendar, c.by, "ics")}


@router.get("/api/v1/export.ics")
async def export_ics(calendar: str | None = None, c: Caller = Depends(caller)):
    async with session_scope() as db:
        data = await ics.export(db, set(calendar.split(",")) if calendar else None)
    return Response(data, media_type="text/calendar; charset=utf-8", headers={"content-disposition": 'attachment; filename="kalender.ics"'})


@router.get("/api/v1/feed")
async def feed_info(request: Request, c: Caller = Depends(caller)):
    if c.by != "user":
        raise Forbidden("Den Abo-Link sieht nur der Nutzer")
    async with session_scope(write=True) as db:
        tok = await cfg.feed_token(db)
    base = get_settings().kalender_public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return {"url": f"{base}/ics/{tok}.ics", "token": tok}


@router.post("/api/v1/feed/rotate")
async def feed_rotate(request: Request, c: Caller = Depends(caller)):
    if c.by != "user":
        raise Forbidden("Den Abo-Link ändert nur der Nutzer")
    async with session_scope(write=True) as db:
        tok = await cfg.rotate_feed_token(db)
    base = get_settings().kalender_public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return {"url": f"{base}/ics/{tok}.ics", "token": tok}


@router.get("/ics/{token}.ics")
async def feed(token: str):
    """Abo-Link fürs Handy (nur lesen). Der Token steht im Pfad; ein falscher gibt 404."""
    async with session_scope() as db:
        if not await cfg.feed_token_ok(db, token):
            raise NotFound("Nicht gefunden")
        data = await ics.export(db)
    return Response(data, media_type="text/calendar; charset=utf-8", headers={"cache-control": "no-store"})
