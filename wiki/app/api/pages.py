"""Seiten-, Ordner- und Papierkorb-Endpunkte (`/api/v1`)."""
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import Caller, caller
from ..config import get_settings
from ..db import session_scope
from ..errors import Forbidden, Invalid, NotFound
from ..models import Page
from ..services import files as file_svc
from ..services import idem, permissions as perm, store
from ..services.vault import normalize_path

router = APIRouter(prefix="/api/v1", tags=["pages"])


class FolderCreateIn(BaseModel):
    folder: str


class PageSaveIn(BaseModel):
    path: str
    title: str | None = None
    body: str = ""
    frontmatter: dict | None = None
    raw_markdown: str | None = None
    note: str = ""
    expected_revision: int | None = None
    force_new: bool = False


class PageUpdateIn(BaseModel):
    path: str | None = None
    title: str | None = None
    body: str = ""
    frontmatter: dict | None = None
    raw_markdown: str | None = None
    note: str = ""
    expected_revision: int | None = None


class PagePatchIn(BaseModel):
    ref: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    heading: str | None = None
    mode: str = "append"
    frontmatter_patch: dict | None = None
    title: str | None = None
    note: str = ""


class PageMoveIn(BaseModel):
    ref: str | None = None
    to_path: str
    title: str | None = None


class PageDeleteIn(BaseModel):
    ref: str
    note: str = ""


@router.get("/tree")
async def get_tree(space: str | None = Query(None), c: Caller = Depends(caller)):
    if space:
        perm.require_read(c, space)
    async with session_scope(write=False) as db:
        return await store.list_tree(db, c, space=space)


@router.post("/folders")
async def create_folder(body: FolderCreateIn, c: Caller = Depends(caller)):
    raw = (body.folder or "").strip().strip("/")
    norm_dummy, space, folder, _ = normalize_path(f"{raw}/_platzhalter")
    perm.require_write(c, space)
    if space == "memory" and c.kind != "user":
        raise Forbidden("In `memory/` sind nur die 7 festen Hauptkategorien erlaubt.")
    target = (get_settings().vault_dir / folder).resolve()
    if not target.is_relative_to(get_settings().vault_dir):
        raise Invalid("Ungültiger Ordnerpfad")
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "folder": folder, "space": space}


@router.get("/pages")
async def list_pages(
    space: str | None = Query(None),
    folder: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    c: Caller = Depends(caller),
):
    if space:
        perm.require_read(c, space)
    async with session_scope(write=False) as db:
        tree = await store.list_tree(db, c, space=space)
        pages = tree["pages"]
        if folder:
            clean_f = folder.strip("/")
            pages = [p for p in pages if p["folder"] == clean_f or p["folder"].startswith(clean_f + "/")]
        return {"pages": pages[:limit]}


@router.get("/page")
async def get_page_by_ref(ref: str = Query(..., min_length=1), c: Caller = Depends(caller)):
    async with session_scope(write=False) as db:
        p = await store.get_page_or_404(db, ref, c)
        return await store.page_detail_dict(db, p, c)


@router.get("/pages/{page_id}")
async def get_page_by_id(page_id: str, c: Caller = Depends(caller)):
    async with session_scope(write=False) as db:
        p = await store.get_page_or_404(db, page_id, c)
        return await store.page_detail_dict(db, p, c)


@router.post("/pages")
async def create_or_save_page(body: PageSaveIn, request: Request, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await idem.run(
            db,
            request,
            lambda: store.save_page(
                db,
                c,
                path=body.path,
                title=body.title,
                body=body.body,
                frontmatter=body.frontmatter,
                raw_markdown=body.raw_markdown,
                note=body.note,
                expected_revision=body.expected_revision,
                force_new=body.force_new,
            ),
        )


@router.put("/pages/{page_id}")
async def update_page(page_id: str, body: PageUpdateIn, request: Request, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        async def _do():
            p = await store.get_page_or_404(db, page_id, c)
            target_path = body.path or p.path
            if target_path != p.path:
                await store.move_page(db, c, p.id, target_path, new_title=body.title)
            return await store.save_page(
                db,
                c,
                path=target_path,
                title=body.title or p.title,
                body=body.body,
                frontmatter=body.frontmatter,
                raw_markdown=body.raw_markdown,
                note=body.note,
                expected_revision=body.expected_revision,
            )
        return await idem.run(db, request, _do)


@router.patch("/pages/{page_id}")
async def patch_page_by_id(page_id: str, body: PagePatchIn, request: Request, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await idem.run(
            db,
            request,
            lambda: store.patch_page(
                db,
                c,
                page_id,
                old_text=body.old_text,
                new_text=body.new_text,
                heading=body.heading,
                mode=body.mode,
                frontmatter_patch=body.frontmatter_patch,
                title=body.title,
                note=body.note,
            ),
        )


@router.post("/pages/patch")
async def patch_page_by_ref(body: PagePatchIn, request: Request, c: Caller = Depends(caller)):
    if not body.ref:
        raise Invalid("ref (Pfad oder Seiten-ID) fehlt")
    async with session_scope(write=True) as db:
        return await idem.run(
            db,
            request,
            lambda: store.patch_page(
                db,
                c,
                body.ref,
                old_text=body.old_text,
                new_text=body.new_text,
                heading=body.heading,
                mode=body.mode,
                frontmatter_patch=body.frontmatter_patch,
                title=body.title,
                note=body.note,
            ),
        )


@router.post("/pages/{page_id}/move")
async def move_page_by_id(page_id: str, body: PageMoveIn, request: Request, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await idem.run(
            db,
            request,
            lambda: store.move_page(db, c, page_id, body.to_path, new_title=body.title),
        )


@router.post("/pages/move")
async def move_page_by_ref(body: PageMoveIn, request: Request, c: Caller = Depends(caller)):
    if not body.ref:
        raise Invalid("ref (Pfad oder Seiten-ID) fehlt")
    async with session_scope(write=True) as db:
        return await idem.run(
            db,
            request,
            lambda: store.move_page(db, c, body.ref, body.to_path, new_title=body.title),
        )


@router.delete("/pages/{page_id}")
async def delete_page_by_id(page_id: str, request: Request, note: str = Query(""), c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await idem.run(db, request, lambda: store.delete_page(db, c, page_id, note=note))


@router.post("/pages/delete")
async def delete_page_by_ref(body: PageDeleteIn, request: Request, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await idem.run(db, request, lambda: store.delete_page(db, c, body.ref, note=body.note))


@router.get("/trash")
async def list_trash(c: Caller = Depends(caller)):
    allowed = [s for s in ("privat", "memory", "ki") if perm.can_read_space(c, s)]
    async with session_scope(write=False) as db:
        rows = (await db.scalars(
            select(Page).where(Page.space.in_(allowed), Page.deleted_at.is_not(None)).order_by(Page.deleted_at.desc())
        )).all()
        return {
            "items": [
                {
                    **store.page_summary_dict(p),
                    "deleted_at": p.deleted_at.isoformat() if p.deleted_at else None,
                }
                for p in rows
            ]
        }


@router.post("/trash/{page_id}/restore")
async def restore_trash(page_id: str, request: Request, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await idem.run(db, request, lambda: store.restore_from_trash(db, c, page_id))


@router.delete("/trash/{page_id}")
async def purge_trash_item(page_id: str, c: Caller = Depends(caller)):
    if c.kind != "user":
        raise Forbidden("Endgültiges Löschen aus dem Papierkorb ist nur dem Nutzer selbst erlaubt.")
    async with session_scope(write=True) as db:
        p = await db.get(Page, page_id)
        if not p or p.deleted_at is None:
            raise NotFound("Eintrag liegt nicht im Papierkorb")
        file_svc.purge_files_for_page(p.id)
        await db.delete(p)
        return {"ok": True, "purged": page_id}
