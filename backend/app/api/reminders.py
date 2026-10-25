"""Reminders: things that happen once („erinnere mich morgen um 9“). Routines (repeating) are in routines.py."""
from fastapi import APIRouter

from ..schemas import ReminderCreate, ReminderUpdate
from ..services import reminders as svc
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/reminders", tags=["reminders"])


@router.get("")
async def list_reminders(db: DB):
    return [ser.reminder(r) for r in await svc.listing(db)]


@router.post("", status_code=201)
async def create(body: ReminderCreate, db: DB):
    return ser.reminder(await svc.create(db, body.model_dump()))


@router.patch("/{reminder_id}")
async def update(reminder_id: str, body: ReminderUpdate, db: DB):
    return ser.reminder(await svc.update(db, reminder_id, body.model_dump(exclude_unset=True)))


@router.delete("/{reminder_id}")
async def delete(reminder_id: str, db: DB):
    await svc.remove(db, reminder_id)
    return {"ok": True}


@router.post("/{reminder_id}/fire")
async def fire_now(reminder_id: str, db: DB):
    """Fire it now instead of at its time (it is then done)."""
    await svc.get(db, reminder_id)
    await db.commit()
    await svc.fire(reminder_id)
    return {"ok": True}
