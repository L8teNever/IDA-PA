"""REST-API der Aufgaben – dieselbe für die Oberfläche, für IDA (Ida, Stella) und Skripte."""
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, or_, select

from . import bus, days
from .auth import Caller, caller
from .config import get_settings
from .db import pending, session_scope, utcnow
from .models import Task

router = APIRouter(prefix="/api/v1")
VIEWS = ("heute", "geplant", "erledigt", "offen", "alle")
TICKET_S = 60


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    note: str = Field("", max_length=5000)
    priority: int = Field(0, ge=0, le=3)
    day: str | None = None
    due: str | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    note: str | None = Field(None, max_length=5000)
    priority: int | None = Field(None, ge=0, le=3)
    day: str | None = None
    due: str | None = None
    done: bool | None = None


def _day(raw, field: str):
    try:
        return days.parse(raw)
    except days.BadDay as e:
        raise HTTPException(400, f"{field}: {e}") from e


def view(t: Task) -> dict:
    today = days.today()
    if t.done_at:
        state = "erledigt"
    elif t.day and t.day > today:
        state = "geplant"
    else:
        state = "heute"
    return {"id": t.id, "title": t.title, "note": t.note, "priority": t.priority,
            "day": t.day.isoformat() if t.day else None, "due": t.due.isoformat() if t.due else None,
            "done": bool(t.done_at), "done_at": t.done_at.isoformat() if t.done_at else None, "state": state,
            "overdue": bool(not t.done_at and t.due and t.due < today),
            "created_by": t.created_by, "updated_by": t.updated_by,
            "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat()}


def _changed(db, ids: list[str], by: str) -> None:
    pending(db).append({"type": "changed", "ids": ids, "by": by})


def _query(which: str):
    today = days.today()
    q = select(Task)
    if which == "heute":
        return q.where(Task.done_at.is_(None), or_(Task.day.is_(None), Task.day <= today)).order_by(
            Task.priority.desc(), Task.due.is_(None), Task.due, Task.created_at)
    if which == "geplant":
        return q.where(Task.done_at.is_(None), Task.day > today).order_by(Task.day, Task.priority.desc(), Task.created_at)
    if which == "erledigt":
        return q.where(Task.done_at.is_not(None)).order_by(Task.done_at.desc())
    if which == "offen":
        return q.where(Task.done_at.is_(None)).order_by(Task.day.is_not(None), Task.day, Task.priority.desc(), Task.created_at)
    return q.order_by(Task.created_at.desc())


async def _purge(db) -> None:
    """Erledigte nach done_keep_days endgültig weg – die Liste „Erledigt“ soll nicht endlos wachsen."""
    cut = utcnow() - timedelta(days=get_settings().done_keep_days)
    await db.execute(delete(Task).where(and_(Task.done_at.is_not(None), Task.done_at < cut)))


@router.get("/health")
async def health():
    return {"ok": True}


@router.get("/info")
async def info():
    return {"today": days.today().isoformat(), "voice": get_settings().voice_on}


@router.get("/tasks")
async def list_tasks(view_: str = Query("heute", alias="view"), q: str = "", day_from: str | None = None, day_to: str | None = None,
                     limit: int = 300, c: Caller = Depends(caller)):
    """view: heute | geplant | erledigt | offen | alle. q: Suchwörter (alle müssen im Titel oder der Notiz stehen).
    day_from/day_to: nur Aufgaben, deren Tag (oder Frist) in diesem Zeitraum liegt."""
    which = view_ if view_ in VIEWS else "heute"
    stmt = _query(which)
    for w in q.lower().split()[:6]:
        stmt = stmt.where(or_(Task.title.ilike(f"%{w}%"), Task.note.ilike(f"%{w}%")))
    lo, hi = _day(day_from, "day_from"), _day(day_to, "day_to")
    if lo:
        stmt = stmt.where(or_(Task.day >= lo, Task.due >= lo))
    if hi:
        stmt = stmt.where(or_(Task.day <= hi, and_(Task.day.is_(None), Task.due <= hi)))
    async with session_scope(write=which == "erledigt") as db:
        if which == "erledigt":
            await _purge(db)
        rows = (await db.scalars(stmt.limit(max(1, min(limit, 500))))).all()
    return {"view": which, "today": days.today().isoformat(), "tasks": [view(t) for t in rows]}


@router.get("/counts")
async def counts():
    async with session_scope() as db:
        out = {}
        for which in ("heute", "geplant", "erledigt"):
            out[which] = len((await db.scalars(_query(which).with_only_columns(Task.id))).all())
    return out


@router.post("/tasks", status_code=201)
async def create(body: TaskIn, c: Caller = Depends(caller)):
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(400, "Der Titel fehlt")
    t = Task(title=title, note=body.note.strip(), priority=body.priority, day=_day(body.day, "day"), due=_day(body.due, "due"),
             created_by=c.by, updated_by=c.by)
    if t.day and t.day <= days.today():
        t.day = None  # heute oder früher = einfach auf der Startseite
    async with session_scope(write=True) as db:
        db.add(t)
        await db.flush()
        _changed(db, [t.id], c.by)
        out = view(t)
    return {"task": out}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, c: Caller = Depends(caller)):
    async with session_scope() as db:
        t = await db.get(Task, task_id)
        if not t:
            raise HTTPException(404, "Aufgabe nicht gefunden")
        return {"task": view(t)}


@router.patch("/tasks/{task_id}")
async def update(task_id: str, body: TaskPatch, c: Caller = Depends(caller)):
    data = body.model_dump(exclude_unset=True)
    async with session_scope(write=True) as db:
        t = await db.get(Task, task_id)
        if not t:
            raise HTTPException(404, "Aufgabe nicht gefunden")
        if data.get("title") is not None:
            t.title = " ".join(data["title"].split()) or t.title
        if data.get("note") is not None:
            t.note = data["note"].strip()
        if data.get("priority") is not None:
            t.priority = data["priority"]
        if "day" in data:
            d = _day(data["day"], "day")
            t.day = None if d and d <= days.today() else d
        if "due" in data:
            t.due = _day(data["due"], "due")
        if data.get("done") is not None:
            t.done_at = (t.done_at or utcnow()) if data["done"] else None
            if not data["done"] and t.day and t.day < days.today():
                t.day = None  # wiederhergestellt: wieder auf die Startseite
        t.updated_by = c.by
        t.updated_at = utcnow()
        await db.flush()
        _changed(db, [t.id], c.by)
        out = view(t)
    return {"task": out}


@router.delete("/tasks/{task_id}")
async def remove(task_id: str, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        t = await db.get(Task, task_id)
        if not t:
            raise HTTPException(404, "Aufgabe nicht gefunden")
        out = view(t)
        await db.delete(t)
        _changed(db, [task_id], c.by)
    return {"ok": True, "task": out}


@router.get("/stream")
async def stream(request: Request, c: Caller = Depends(caller)):
    """Server-Sent Events: {"type": "changed", "ids": […], "by": "user"|Agent}. Die Oberfläche lädt dann neu."""
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), 20)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


def make_ticket(secret: str, now: float | None = None) -> str:
    """Gleiches Format wie app/voice/web.py (exp.nonce.sig): der Voice-Dienst prüft es mit demselben VOICE_SECRET."""
    exp = int((now or time.time()) + TICKET_S)
    nonce = secrets.token_hex(8)
    sig = hmac.new(secret.encode(), f"{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{nonce}.{sig}"


@router.post("/voice/ticket")
async def voice_ticket(c: Caller = Depends(caller)):
    """Ein Ticket für ein Gespräch mit Stella im Aufgaben-Modus. Nur die Oberfläche selbst (Nutzer), nie ein Agent."""
    s = get_settings()
    if c.kind != "user":
        raise HTTPException(403, "Nur aus der App")
    if not s.voice_on:
        raise HTTPException(404, "Sprechen ist nicht eingerichtet (VOICE_SECRET und VOICE_PUBLIC_URL fehlen)")
    base = s.voice_public_url.rstrip("/")
    ws = ("wss://" + base[8:]) if base.startswith("https://") else ("ws://" + base.removeprefix("http://"))
    return {"ticket": make_ticket(s.voice_secret), "url": f"{ws}/live", "mode": "tasks"}
