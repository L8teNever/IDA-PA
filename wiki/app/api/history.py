"""Versionsverlauf (global & pro Seite), Diff-Ansicht, 1-Klick-Wiederherstellung und Undo."""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from ..auth import Caller, caller
from ..db import session_scope
from ..errors import NotFound
from ..models import Page, Revision
from ..services import idem, permissions as perm, store

router = APIRouter(prefix="/api/v1", tags=["history"])


@router.get("/history")
async def global_history(
    space: str | None = Query(None),
    by: str | None = Query(None),
    via: str | None = Query(None),
    page_id: str | None = Query(None),
    before_id: int | None = Query(None),
    limit: int = Query(80, ge=1, le=300),
    c: Caller = Depends(caller),
):
    allowed = [s for s in ("privat", "memory", "ki") if perm.can_read_space(c, s) and (not space or s == space)]
    if not allowed:
        return {"revisions": []}
    async with session_scope(write=False) as db:
        stmt = select(Revision).where(Revision.space.in_(allowed)).order_by(Revision.id.desc()).limit(limit)
        if by:
            if by == "_ai_":
                stmt = stmt.where(Revision.by != "user")
            else:
                stmt = stmt.where(Revision.by == by)
        if via:
            stmt = stmt.where(Revision.via == via)
        if page_id:
            stmt = stmt.where(Revision.page_id == page_id)
        if before_id:
            stmt = stmt.where(Revision.id < before_id)
        rows = (await db.scalars(stmt)).all()
        return {"revisions": [store.revision_view(r) for r in rows]}


@router.get("/pages/{page_id}/revisions")
async def page_revisions(page_id: str, c: Caller = Depends(caller)):
    async with session_scope(write=False) as db:
        p = await store.get_page_or_404(db, page_id, c)
        rows = (await db.scalars(
            select(Revision).where(Revision.page_id == p.id).order_by(Revision.rev_number.desc())
        )).all()
        return {"page_id": p.id, "path": p.path, "revisions": [store.revision_view(r) for r in rows]}


@router.get("/revisions/{rev_id}")
async def get_revision_detail(rev_id: int, compare_to: int | None = Query(None), c: Caller = Depends(caller)):
    async with session_scope(write=False) as db:
        rev = await db.get(Revision, rev_id)
        if not rev:
            raise NotFound("Revision nicht gefunden")
        perm.require_read(c, rev.space)

        if compare_to is not None:
            prev = await db.get(Revision, compare_to)
            if not prev or prev.page_id != rev.page_id:
                raise NotFound("Vergleichs-Revision nicht gefunden")
        else:
            prev = (await db.scalars(
                select(Revision)
                .where(Revision.page_id == rev.page_id, Revision.rev_number < rev.rev_number)
                .order_by(Revision.rev_number.desc())
                .limit(1)
            )).first()

        before_txt = prev.content_full if prev else ""
        after_txt = "" if rev.action == "delete" else rev.content_full
        diff_text, added, deleted = store.unified_diff(before_txt, after_txt, f"{rev.path_snapshot}.md")

        page = await db.get(Page, rev.page_id)
        return {
            **store.revision_view(rev),
            "content_full": rev.content_full,
            "before_content": before_txt,
            "diff": diff_text,
            "diff_added": added,
            "diff_deleted": deleted,
            "compared_to_rev": prev.rev_number if prev else 0,
            "page_deleted": bool(page and page.deleted_at is not None),
        }


@router.post("/pages/{page_id}/restore/{rev_id}")
async def restore_page_revision(page_id: str, rev_id: int, request: Request, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await idem.run(db, request, lambda: store.restore_revision(db, c, page_id, rev_id))


@router.post("/revisions/{rev_id}/undo")
async def undo_single_revision(rev_id: int, request: Request, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await idem.run(db, request, lambda: store.undo_revision(db, c, rev_id))
