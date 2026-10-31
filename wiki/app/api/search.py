"""Volltextsuche, `[[`-Vorschläge, Tags, Wissensgraph & System-Prompt-Gedächtniskontext."""
from fastapi import APIRouter, Depends, Query

from ..auth import Caller, caller
from ..db import session_scope
from ..services import permissions as perm, search as search_svc

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
async def search_endpoint(
    q: str = Query(""),
    space: str | None = Query(None),
    tag: str | None = Query(None),
    folder: str | None = Query(None),
    limit: int = Query(40, ge=1, le=200),
    c: Caller = Depends(caller),
):
    spaces = [s.strip() for s in space.split(",") if s.strip()] if space else None
    if spaces:
        for sp in spaces:
            perm.require_read(c, sp)
    async with session_scope(write=False) as db:
        hits = await search_svc.search_pages(db, c, q, spaces=spaces, tag=tag, folder=folder, limit=limit)
        return {"results": hits}


@router.get("/suggest")
async def suggest_endpoint(q: str = Query(""), limit: int = Query(25, ge=1, le=80), c: Caller = Depends(caller)):
    async with session_scope(write=False) as db:
        return {"items": await search_svc.suggest_links(db, c, q, limit=limit)}


@router.get("/tags")
async def tags_endpoint(c: Caller = Depends(caller)):
    async with session_scope(write=False) as db:
        return {"tags": await search_svc.list_tags(db, c)}


@router.get("/graph")
async def graph_endpoint(
    space: str | None = Query(None),
    tag: str | None = Query(None),
    include_attachments: bool = Query(False),
    include_ghosts: bool = Query(True),
    include_orphans: bool = Query(True),
    center: str | None = Query(None),
    depth: int = Query(2, ge=1, le=4),
    c: Caller = Depends(caller),
):
    spaces = [s.strip() for s in space.split(",") if s.strip()] if space else None
    if spaces:
        for sp in spaces:
            perm.require_read(c, sp)
    async with session_scope(write=False) as db:
        return await search_svc.build_graph(
            db,
            c,
            spaces=spaces,
            tag=tag,
            include_attachments=include_attachments,
            include_ghosts=include_ghosts,
            include_orphans=include_orphans,
            center_ref=center,
            depth=depth,
        )


@router.get("/memory/context")
async def memory_context_endpoint(
    agent_id: str = Query("main"),
    q: str = Query(""),
    max_chars: int = Query(3800, ge=400, le=12000),
    c: Caller = Depends(caller),
):
    async with session_scope(write=False) as db:
        return await search_svc.memory_context_for_ida(db, c, agent_id=agent_id, query=q, max_chars=max_chars)
