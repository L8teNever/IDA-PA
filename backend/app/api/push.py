"""Push notifications for the messages app: VAPID key, (un)subscribing devices, test push."""
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import delete, select

from ..models import PushSubscription
from ..services import push as push_svc
from ..services.errors import Invalid
from .deps import DB

router = APIRouter(prefix="/api/v1/push", tags=["push"])


class Keys(BaseModel):
    p256dh: str
    auth: str


class Subscription(BaseModel):
    endpoint: str
    keys: Keys | None = None
    device: str = ""


@router.get("/key")
async def key(db: DB):
    return {"key": await push_svc.public_key(db)}


@router.get("/devices")
async def devices(db: DB):
    rows = (await db.scalars(select(PushSubscription).order_by(PushSubscription.created_at))).all()
    return [{"id": s.id, "device": s.device, "created_at": s.created_at.isoformat(), "last_ok": s.last_ok.isoformat() if s.last_ok else None} for s in rows]


@router.post("/subscribe")
async def subscribe(body: Subscription, request: Request, db: DB):
    if not body.endpoint.startswith(("https://", "http://127.0.0.1")) or not body.keys:  # http nur lokal (Tests)
        raise Invalid("Ungültiges Push-Abo")
    device = (body.device or request.headers.get("user-agent", ""))[:200]
    s = await db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if s:
        s.p256dh, s.auth, s.device = body.keys.p256dh, body.keys.auth, device
    else:
        s = PushSubscription(endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth, device=device)
        db.add(s)
    await db.flush()
    return {"ok": True, "id": s.id}


@router.post("/unsubscribe")
async def unsubscribe(body: Subscription, db: DB):
    await db.execute(delete(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    return {"ok": True}


@router.post("/test")
async def test(db: DB):
    return await push_svc.test(db)
