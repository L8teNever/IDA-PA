"""Offline-Delta-Sync, SSE-Live-Stream, Freigabe-Proposals, MCP-Zugänge, Backups, ZIP-Export & „Ida fragen“."""
import asyncio
import json

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from ..auth import Caller, caller, create_named_mcp_token
from ..config import get_settings
from ..db import session_scope, utcnow
from ..errors import Forbidden, Invalid, NotFound
from ..models import McpToken, Page, Proposal, Revision
from ..services import backup, bus, permissions as perm, proposals, store

router = APIRouter(prefix="/api/v1", tags=["sync"])


class McpTokenCreateIn(BaseModel):
    name: str
    allow_read_private: bool = True
    allow_read_chats: bool = True


class AskIdaIn(BaseModel):
    question: str
    page_path: str | None = None
    page_title: str | None = None
    page_excerpt: str | None = None


@router.get("/health")
async def health():
    return {"ok": True, "service": "ida-wiki"}


@router.get("/meta")
async def meta(request: Request, c: Caller = Depends(caller)):
    s = get_settings()
    async with session_scope(write=False) as db:
        pending_props = await db.scalar(select(func.count()).select_from(Proposal).where(Proposal.status == "pending")) or 0
    return {
        "version": getattr(request.app.state, "version", "dev"),
        "timezone": s.timezone,
        "public_url": s.wiki_public_url,
        "ida_connected": bool(s.ida_internal_url and s.wiki_ida_token),
        "mcp_master_configured": bool(s.wiki_mcp_token),
        "max_file_mb": s.wiki_max_file_mb,
        "spaces": [sp for sp in ("privat", "memory", "ki") if perm.can_read_space(c, sp)],
        "memory_categories": list(perm.MEMORY_CATEGORIES),
        "privat_folders": list(perm.PRIVAT_DEFAULT_FOLDERS),
        "pending_proposals": pending_props if c.kind == "user" else 0,
    }


@router.get("/sync")
async def delta_sync(since: int = Query(0, ge=0), c: Caller = Depends(caller)):
    """Offline-First-Synchronisation für die PWA:
    * `since == 0` → kompletter Bestand aller lesbaren Seiten (inkl. Body, Backlinks, Anhänge) + Ordnerstruktur
    * `since > 0`  → nur seit Revision `since` geänderte oder gelöschte Seiten.
    """
    allowed = [sp for sp in ("privat", "memory", "ki") if perm.can_read_space(c, sp)]
    async with session_scope(write=False) as db:
        max_rev = await db.scalar(select(func.max(Revision.id))) or 0
        tree = await store.list_tree(db, c)
        pending_props = (
            await db.scalar(select(func.count()).select_from(Proposal).where(Proposal.status == "pending")) or 0
        ) if c.kind == "user" else 0

        if since == 0:
            active_rows = (await db.scalars(
                select(Page).where(Page.space.in_(allowed), Page.deleted_at.is_(None)).order_by(Page.path)
            )).all()
            full_pages = [await store.page_detail_dict(db, p, c) for p in active_rows]
            return {
                "full": True,
                "cursor": max_rev,
                "spaces": tree["spaces"],
                "folders": tree["folders"],
                "pages": full_pages,
                "deleted_ids": [],
                "pending_proposals": pending_props,
            }

        changed_pids = list((await db.scalars(
            select(Revision.page_id).where(Revision.id > since, Revision.space.in_(allowed)).distinct()
        )).all())
        upserts = []
        deleted_ids = []
        if changed_pids:
            pages = (await db.scalars(select(Page).where(Page.id.in_(changed_pids)))).all()
            for p in pages:
                if p.deleted_at is not None or not perm.can_read_space(c, p.space):
                    deleted_ids.append(p.id)
                else:
                    upserts.append(await store.page_detail_dict(db, p, c))

        return {
            "full": False,
            "cursor": max_rev,
            "spaces": tree["spaces"],
            "folders": tree["folders"],
            "pages": upserts,
            "deleted_ids": deleted_ids,
            "pending_proposals": pending_props,
        }


@router.get("/stream")
async def sse_stream(request: Request, c: Caller = Depends(caller)):
    last_id_hdr = request.headers.get("last-event-id") or request.query_params.get("since") or ""
    last_rev = int(last_id_hdr) if last_id_hdr.isdigit() else 0

    async def event_gen():
        q = bus.subscribe()
        try:
            if last_rev > 0:
                async with session_scope(write=False) as db:
                    cur = await db.scalar(select(func.max(Revision.id))) or 0
                if cur > last_rev:
                    yield f"id: {cur}\ndata: {json.dumps({'type': 'resync', 'cursor': cur})}\n\n"
            yield f"data: {json.dumps({'type': 'ready'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=25.0)
                    space = evt.get("space")
                    if space and not perm.can_read_space(c, space):
                        continue
                    rid = evt.get("revision_id")
                    prefix = f"id: {rid}\n" if rid else ""
                    yield f"{prefix}data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/proposals")
async def list_proposals(status: str = Query("pending"), c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("Freigabe-Übersicht ist nur für den Nutzer sichtbar.")
    async with session_scope(write=False) as db:
        stmt = select(Proposal).order_by(Proposal.created_at.desc()).limit(100)
        if status != "all":
            stmt = stmt.where(Proposal.status == status)
        rows = (await db.scalars(stmt)).all()
        return {"proposals": [proposals.proposal_view(r) for r in rows]}


@router.post("/proposals/{prop_id}/approve")
async def user_approve_proposal(prop_id: str, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await proposals.execute_proposal(db, prop_id, ui_caller=c)


@router.post("/proposals/{prop_id}/reject")
async def user_reject_proposal(prop_id: str, c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("Nur der Nutzer kann Vorschläge ablehnen.")
    async with session_scope(write=True) as db:
        return await proposals.reject_proposal(db, prop_id)


@router.get("/mcp-tokens")
async def list_mcp_tokens(c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("MCP-Zugänge können nur vom Nutzer verwaltet werden.")
    async with session_scope(write=False) as db:
        rows = (await db.scalars(select(McpToken).order_by(McpToken.created_at.desc()))).all()
        return {
            "tokens": [
                {
                    "id": r.id,
                    "name": r.name,
                    "prefix": r.prefix,
                    "allow_read_private": r.allow_read_private,
                    "allow_read_chats": r.allow_read_chats,
                    "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        }


@router.post("/mcp-tokens")
async def add_mcp_token(body: McpTokenCreateIn, c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("MCP-Zugänge können nur vom Nutzer erstellt werden.")
    async with session_scope(write=True) as db:
        return await create_named_mcp_token(
            db,
            body.name,
            allow_read_private=body.allow_read_private,
            allow_read_chats=body.allow_read_chats,
        )


@router.delete("/mcp-tokens/{token_id}")
async def revoke_mcp_token(token_id: str, c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("MCP-Zugänge können nur vom Nutzer widerrufen werden.")
    async with session_scope(write=True) as db:
        row = await db.get(McpToken, token_id)
        if not row:
            raise NotFound("MCP-Token nicht gefunden")
        await db.delete(row)
        return {"ok": True, "deleted": token_id}


@router.get("/backups")
async def get_backups(c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("Sicherungen sind nur für den Nutzer zugänglich.")
    return {"backups": await asyncio.to_thread(backup.list_backups)}


@router.post("/backups")
async def trigger_backup(c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("Sicherungen können nur vom Nutzer ausgelöst werden.")
    info = await asyncio.to_thread(backup.run_backup)
    return {"backup": info}


@router.get("/export.zip")
async def export_vault_zip(c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("Vollständiger Vault-Export ist nur dem Nutzer erlaubt.")
    data = await asyncio.to_thread(backup.build_export_zip_bytes)
    stamp = utcnow().strftime("%Y-%m-%d")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="ida-wiki-export-{stamp}.zip"'},
    )


@router.post("/ask")
async def ask_ida(body: AskIdaIn, c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("Nur für die Wiki-Oberfläche verfügbar.")
    q = (body.question or "").strip()
    if not q:
        raise Invalid("Bitte eine Frage eingeben.")
    s = get_settings()
    if not s.ida_internal_url or not s.wiki_ida_token:
        raise Invalid("Die Verbindung zu IDA (IDA_INTERNAL_URL / WIKI_IDA_TOKEN) ist nicht eingerichtet.")

    url = s.ida_internal_url.rstrip("/") + "/api/v1/ext/wiki/ask"
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {s.wiki_ida_token}"},
                json={
                    "question": q,
                    "page_path": body.page_path,
                    "page_title": body.page_title,
                    "page_excerpt": (body.page_excerpt or "")[:4000],
                },
            )
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text) if "json" in r.headers.get("content-type", "") else r.text
            raise Invalid(f"IDA konnte die Anfrage nicht beantworten: {detail}")
        return r.json()
    except httpx.HTTPError as e:
        raise Invalid(f"IDA ist momentan nicht erreichbar ({e}).") from e
