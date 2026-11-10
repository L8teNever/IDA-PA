"""Smart Boards (nur Wiki-Oberfläche): Thema + Modell eingeben, Ida befüllt das Board, „Neu laden“ aktualisiert es."""
import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import Caller, caller
from ..config import get_settings
from ..db import session_scope
from ..errors import Forbidden, Invalid, NotFound
from ..models import Board
from ..services import boards as svc

router = APIRouter(prefix="/api/v1/boards", tags=["boards"])


class BoardIn(BaseModel):
    title: str = Field("", max_length=120)
    prompt: str = Field("", max_length=4000)
    model: str | None = Field(None, max_length=80)


class BoardPatch(BaseModel):
    title: str | None = Field(None, max_length=120)
    prompt: str | None = Field(None, max_length=4000)
    model: str | None = Field(None, max_length=80)


def _user(c: Caller) -> None:
    if c.kind != "user":
        raise Forbidden("Nur für die Wiki-Oberfläche verfügbar.")


def _ser(b: Board, full: bool = True) -> dict:
    d = {"id": b.id, "title": b.title, "prompt": b.prompt, "model": b.model, "status": b.status, "error": b.error,
         "last_run_at": b.last_run_at.isoformat() if b.last_run_at else None, "run_count": b.run_count,
         "created_at": b.created_at.isoformat(), "preview": " ".join(b.content.split())[:140]}
    if full:
        d["content"] = b.content
    return d


@router.get("")
async def list_boards(c: Caller = Depends(caller)):
    _user(c)
    async with session_scope() as db:
        rows = (await db.scalars(select(Board).order_by(Board.created_at.desc()))).all()
    return {"boards": [_ser(b, full=False) for b in rows], "connected": bool(get_settings().ida_chat_url)}


@router.get("/models")
async def models(c: Caller = Depends(caller)):
    _user(c)
    base = get_settings().ida_chat_url.rstrip("/")
    if not base:
        return {"models": [], "model_names": {}, "default_model": None}
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10) as cl:
            r = await cl.get("/api/v1/bootstrap")
        r.raise_for_status()
        d = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise Invalid("Ida ist gerade nicht erreichbar") from e
    return {k: d.get(k) for k in ("models", "model_names", "default_model")}


@router.post("")
async def create(body: BoardIn, c: Caller = Depends(caller)):
    _user(c)
    prompt = body.prompt.strip()
    if not prompt:
        raise Invalid("Bitte ein Thema oder einen Prompt eingeben.")
    title = body.title.strip() or " ".join(prompt.split())[:60]
    async with session_scope(write=True) as db:
        b = Board(title=title, prompt=prompt, model=(body.model or None))
        db.add(b)
        await db.flush()
        out = _ser(b)
    await svc.start(out["id"])
    out["status"] = "running"
    return out


@router.get("/{board_id}")
async def get_board(board_id: str, c: Caller = Depends(caller)):
    _user(c)
    async with session_scope() as db:
        b = await db.get(Board, board_id)
        if not b:
            raise NotFound("Board nicht gefunden")
        return _ser(b)


@router.patch("/{board_id}")
async def update(board_id: str, body: BoardPatch, c: Caller = Depends(caller)):
    _user(c)
    async with session_scope(write=True) as db:
        b = await db.get(Board, board_id)
        if not b:
            raise NotFound("Board nicht gefunden")
        if body.title is not None and body.title.strip():
            b.title = body.title.strip()
        if body.prompt is not None:
            if not body.prompt.strip():
                raise Invalid("Der Prompt darf nicht leer sein.")
            b.prompt = body.prompt.strip()
        if "model" in body.model_fields_set:
            b.model = body.model or None
        return _ser(b)


@router.post("/{board_id}/refresh")
async def refresh(board_id: str, c: Caller = Depends(caller)):
    _user(c)
    async with session_scope() as db:
        if not await db.get(Board, board_id):
            raise NotFound("Board nicht gefunden")
    started = await svc.start(board_id)
    return {"ok": True, "started": started}


@router.delete("/{board_id}")
async def delete(board_id: str, c: Caller = Depends(caller)):
    _user(c)
    t = svc._tasks.pop(board_id, None)
    if t:
        t.cancel()
    async with session_scope(write=True) as db:
        b = await db.get(Board, board_id)
        if not b:
            raise NotFound("Board nicht gefunden")
        await db.delete(b)
    return {"ok": True}
