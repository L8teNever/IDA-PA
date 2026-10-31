"""Zentraler Speicher für Seiten, Verlinkungen, Volltextindex, Papierkorb und Versionsverlauf."""
import difflib
import hashlib
import re
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Caller
from ..config import get_settings
from ..db import pending, utcnow
from ..errors import Conflict, Invalid, NotFound
from ..models import SPACES, Attachment, Page, PageLink, PageTag, Revision
from . import links as link_svc
from . import memory_guard
from . import permissions as perm
from .vault import (
    compose_markdown,
    delete_vault_file,
    file_for,
    normalize_path,
    parse_markdown,
    read_vault_file,
    slugify,
    write_vault_file,
)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def unified_diff(before: str, after: str, path: str = "seite.md") -> tuple[str, int, int]:
    """Berechnet Unified-Diff sowie Anzahl hinzugefügter und gelöschter Zeilen."""
    a_lines = (before or "").splitlines(keepends=True)
    b_lines = (after or "").splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(a_lines, b_lines, fromfile=f"a/{path}", tofile=f"b/{path}", n=3))
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    deleted = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
    return "".join(diff_lines), added, deleted


def three_way_merge(base: str, server: str, client: str) -> str | None:
    """Einfacher zeilenbasierter 3-Wege-Merge für Offline-Änderungen:
    gelingt automatisch, wenn Server und Client unterschiedliche Bereiche der Seite geändert haben.
    Gibt `None` zurück, wenn sich Änderungen überlappen (→ 409 Conflict).
    """
    if server == base:
        return client
    if client == base or client == server:
        return server
    b = base.splitlines()
    s = server.splitlines()
    c = client.splitlines()
    sm_s = difflib.SequenceMatcher(a=b, b=s, autojunk=False).get_opcodes()
    sm_c = difflib.SequenceMatcher(a=b, b=c, autojunk=False).get_opcodes()

    edits_s = [(i1, i2, s[j1:j2]) for tag, i1, i2, j1, j2 in sm_s if tag != "equal"]
    edits_c = [(i1, i2, c[j1:j2]) for tag, i1, i2, j1, j2 in sm_c if tag != "equal"]

    # Überlappungsprüfung auf den Basis-Zeilenbereichen
    for sa, sb, srep in edits_s:
        for ca, cb, crep in edits_c:
            if sa == ca and sb == cb and srep == crep:
                continue
            if max(sa, ca) < min(sb, cb) or (sa == sb == ca) or (ca == cb == sa):
                return None

    combined: dict[tuple[int, int, str], tuple[int, int, list[str]]] = {}
    for a1, a2, rep in edits_s:
        combined[(a1, a2, "s")] = (a1, a2, rep)
    for a1, a2, rep in edits_c:
        if not any(x1 == a1 and x2 == a2 and xrep == rep for x1, x2, xrep in edits_s):
            combined[(a1, a2, "c")] = (a1, a2, rep)

    ordered = sorted(combined.values(), key=lambda x: (x[0], x[1]))
    out: list[str] = []
    pos = 0
    for a1, a2, rep in ordered:
        if a1 < pos:
            return None
        out.extend(b[pos:a1])
        out.extend(rep)
        pos = a2
    out.extend(b[pos:])
    return "\n".join(out) + ("\n" if client.endswith("\n") or server.endswith("\n") else "")


async def _update_fts(db: AsyncSession, page: Page, tags: list[str], headings: list[dict], body: str) -> None:
    await db.execute(text("DELETE FROM pages_fts WHERE page_id = :pid"), {"pid": page.id})
    if page.deleted_at is None:
        await db.execute(
            text(
                "INSERT INTO pages_fts (page_id, title, summary, tags_text, headings_text, body) "
                "VALUES (:pid, :title, :summary, :tags, :heads, :body)"
            ),
            {
                "pid": page.id,
                "title": page.title,
                "summary": page.summary or "",
                "tags": " ".join(tags),
                "heads": " ".join(h["text"] for h in headings),
                "body": body,
            },
        )


async def _lookup_maps(db: AsyncSession) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]]]:
    rows = (await db.execute(select(Page.id, Page.path, Page.space, Page.slug).where(Page.deleted_at.is_(None)))).all()
    by_path = {r.path.lower(): r.id for r in rows}
    by_slug: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        by_slug.setdefault(r.slug, []).append((r.space, r.id))
    return by_path, by_slug


async def _reindex_links_for_page(
    db: AsyncSession,
    page: Page,
    body: str,
    by_path: dict[str, str] | None = None,
    by_slug: dict[str, list[tuple[str, str]]] | None = None,
) -> None:
    await db.execute(delete(PageLink).where(PageLink.source_page_id == page.id))
    if page.deleted_at is not None:
        return
    if by_path is None or by_slug is None:
        by_path, by_slug = await _lookup_maps(db)
    for lk in link_svc.extract_links(body):
        tid = link_svc.resolve_target(lk.target_raw, page.space, by_path, by_slug)
        if tid == page.id:
            tid = None
        db.add(
            PageLink(
                source_page_id=page.id,
                target_page_id=tid,
                target_raw=lk.target_raw,
                heading=lk.heading,
                alias=lk.alias,
                is_embed=lk.is_embed,
                context_snippet=lk.snippet,
            )
        )


async def _resolve_dangling_links(db: AsyncSession, new_page: Page) -> None:
    """Wenn eine vorher als Geister-Link referenzierte Seite neu entsteht, werden offene Links aufgelöst."""
    dangling = (await db.scalars(select(PageLink).where(PageLink.target_page_id.is_(None)))).all()
    if not dangling:
        return
    by_path, by_slug = await _lookup_maps(db)
    src_spaces = dict((await db.execute(select(Page.id, Page.space))).all())
    for lk in dangling:
        tid = link_svc.resolve_target(lk.target_raw, src_spaces.get(lk.source_page_id, "privat"), by_path, by_slug)
        if tid == new_page.id:
            lk.target_page_id = new_page.id


def page_summary_dict(p: Page, tags: list[str] | None = None) -> dict:
    return {
        "id": p.id,
        "space": p.space,
        "path": p.path,
        "folder": p.folder,
        "slug": p.slug,
        "title": p.title,
        "summary": p.summary,
        "pinned": p.pinned,
        "importance": p.importance,
        "word_count": p.word_count,
        "managed_by": p.managed_by,
        "revision_id": p.current_revision_id,
        "created_by": p.created_by,
        "updated_by": p.updated_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        **({"tags": tags} if tags is not None else {}),
    }


async def page_detail_dict(db: AsyncSession, p: Page, caller: Caller) -> dict:
    raw = read_vault_file(p.path)
    fm, body = parse_markdown(raw)
    tags = list((await db.scalars(select(PageTag.tag).where(PageTag.page_id == p.id).order_by(PageTag.tag))).all())
    atts = (await db.scalars(select(Attachment).where(Attachment.page_id == p.id).order_by(Attachment.created_at))).all()

    # Ausgehende Links
    out_rows = (await db.execute(
        select(PageLink, Page.path, Page.title, Page.space)
        .outerjoin(Page, Page.id == PageLink.target_page_id)
        .where(PageLink.source_page_id == p.id)
    )).all()
    outgoing = [
        {
            "target_raw": lk.target_raw,
            "target_id": lk.target_page_id,
            "target_path": tp if tp and perm.can_read_space(caller, ts) else None,
            "target_title": tt if tt and perm.can_read_space(caller, ts) else None,
            "heading": lk.heading,
            "alias": lk.alias,
            "is_embed": lk.is_embed,
            "exists": bool(tp and perm.can_read_space(caller, ts)),
        }
        for lk, tp, tt, ts in out_rows
    ]

    # Eingehende Backlinks (nur aus Spaces, die der Aufrufer lesen darf)
    in_rows = (await db.execute(
        select(PageLink, Page.id, Page.path, Page.title, Page.space)
        .join(Page, Page.id == PageLink.source_page_id)
        .where(PageLink.target_page_id == p.id, Page.deleted_at.is_(None))
    )).all()
    backlinks = [
        {
            "source_id": sid,
            "source_path": spath,
            "source_title": stitle,
            "source_space": sspace,
            "snippet": lk.context_snippet,
        }
        for lk, sid, spath, stitle, sspace in in_rows
        if perm.can_read_space(caller, sspace)
    ]

    rev_count = await db.scalar(select(func.count()).select_from(Revision).where(Revision.page_id == p.id)) or 0

    return {
        **page_summary_dict(p, tags),
        "frontmatter": fm or p.frontmatter or {},
        "body": body,
        "raw": raw,
        "headings": link_svc.extract_headings(body),
        "outgoing_links": outgoing,
        "backlinks": backlinks,
        "attachments": [
            {
                "id": a.id,
                "name": a.name,
                "mime": a.mime,
                "kind": a.kind,
                "size": a.size,
                "uploaded_by": a.uploaded_by,
                "created_at": a.created_at.isoformat(),
                "url": f"/api/v1/files/{p.id}/{a.name}",
            }
            for a in atts
        ],
        "revisions_count": rev_count,
    }


async def get_page_or_404(db: AsyncSession, ref: str, caller: Caller) -> Page:
    """Findet aktive Seite per `id` ODER `path`."""
    p = None
    clean = (ref or "").strip().strip("/")
    if clean.lower().endswith(".md"):
        clean = clean[:-3]
    if "/" not in clean and len(clean) == 12:
        p = await db.get(Page, clean)
    if not p:
        p = await db.scalar(select(Page).where(func.lower(Page.path) == clean.lower()))
    if not p or p.deleted_at is not None:
        raise NotFound(f"Seite „{ref}“ nicht gefunden")
    perm.require_read(caller, p.space)
    return p


async def save_page(
    db: AsyncSession,
    caller: Caller,
    *,
    path: str,
    title: str | None = None,
    body: str = "",
    frontmatter: dict | None = None,
    raw_markdown: str | None = None,
    note: str = "",
    expected_revision: int | None = None,
    approved_by: str | None = None,
    approved_proposal: bool = False,
    force_new: bool = False,
    managed_by: str | None = None,
    coalesce_minutes: int = 0,
) -> dict:
    """Erstellt oder aktualisiert eine Seite atomar (Vault-Datei + SQLite + FTS5 + Links + Revision)."""
    norm_path, space, folder, slug = normalize_path(path)
    perm.require_write(caller, space, approved_proposal=approved_proposal)

    if raw_markdown is not None:
        parsed_fm, body = parse_markdown(raw_markdown)
        fm = {**parsed_fm, **(frontmatter or {})}
    else:
        fm = dict(frontmatter or {})

    existing = await db.scalar(select(Page).where(func.lower(Page.path) == norm_path.lower()))
    creating = existing is None or existing.deleted_at is not None

    eff_title = (title or fm.get("title") or "").strip()
    if not eff_title:
        # Aus erster H1-Überschrift oder Dateinamen ableiten
        m = re.search(r"^#\s+(.+?)\s*$", body or "", re.MULTILINE)
        eff_title = (m.group(1).strip() if m else norm_path.split("/")[-1].replace("-", " ").title())[:200]
    eff_title = eff_title[:200]
    fm["title"] = eff_title

    if space == "memory" and norm_path != "memory/_index":
        if creating and caller.kind in ("ida", "mcp"):
            await memory_guard.check_near_duplicate(db, norm_path, eff_title, force_new=force_new)
        fm = memory_guard.validate_memory_write(caller, norm_path, eff_title, body, fm, creating=creating)

    full_md = compose_markdown(fm, body)
    old_md = read_vault_file(existing.path) if existing and existing.deleted_at is None else ""

    merged = False
    if existing and existing.deleted_at is None and expected_revision is not None and existing.current_revision_id != expected_revision:
        base_rev = await db.get(Revision, expected_revision)
        if base_rev and base_rev.page_id == existing.id:
            merged_md = three_way_merge(base_rev.content_full, old_md, full_md)
            if merged_md is not None:
                full_md = merged_md
                fm, body = parse_markdown(full_md)
                merged = True
            else:
                raise Conflict(
                    "Die Seite wurde zwischenzeitlich an derselben Stelle geändert.",
                    extra={"server": await page_detail_dict(db, existing, caller)},
                )
        else:
            raise Conflict(
                "Die Seite wurde zwischenzeitlich auf dem Server geändert.",
                extra={"server": await page_detail_dict(db, existing, caller)},
            )

    # Unveränderter Inhalt (z. B. wiederholter Sync): nichts unnötig in die History schreiben
    if existing and existing.deleted_at is None and full_md == old_md and existing.title == eff_title:
        return {"page": await page_detail_dict(db, existing, caller), "revision_id": existing.current_revision_id, "unchanged": True}

    _, added, deleted = unified_diff(old_md, full_md, f"{norm_path}.md")
    now = utcnow()
    summary = str(fm.get("summary") or "").strip()[:300]
    if not summary:
        first_p = next(
            (ln.strip(" #>*-\t") for ln in (body or "").splitlines() if ln.strip() and not ln.strip().startswith(("#", "---", "```", "!"))),
            "",
        )
        summary = first_p[:220]

    pinned = bool(fm.get("pinned", existing.pinned if existing else False))
    importance = int(fm.get("importance", existing.importance if existing else 3))
    expires_at = None
    if fm.get("expires_at"):
        try:
            dt = datetime.fromisoformat(str(fm["expires_at"]))
            expires_at = dt if dt.tzinfo else dt.replace(tzinfo=now.tzinfo)
        except ValueError:
            expires_at = None

    write_vault_file(norm_path, full_md)

    if existing is None:
        page = Page(
            space=space,
            path=norm_path,
            folder=folder,
            slug=slug,
            title=eff_title,
            summary=summary,
            frontmatter=fm,
            body_hash=_sha256(body),
            word_count=len((body or "").split()),
            pinned=pinned,
            importance=importance,
            expires_at=expires_at,
            managed_by=managed_by,
            created_by=caller.by,
            updated_by=caller.by,
            created_at=now,
            updated_at=now,
        )
        db.add(page)
        await db.flush()
        action = "create"
    else:
        was_deleted = existing.deleted_at is not None
        page = existing
        if page.path != norm_path and not was_deleted:
            delete_vault_file(page.path)
        page.space = space
        page.path = norm_path
        page.folder = folder
        page.slug = slug
        page.title = eff_title
        page.summary = summary
        page.frontmatter = fm
        page.body_hash = _sha256(body)
        page.word_count = len((body or "").split())
        page.pinned = pinned
        page.importance = importance
        page.expires_at = expires_at
        if managed_by is not None:
            page.managed_by = managed_by
        page.deleted_at = None
        page.updated_by = caller.by
        page.updated_at = now
        action = "create" if was_deleted else "update"

    # Tags & Überschriften & FTS5
    tags = link_svc.extract_tags(fm, body)
    await db.execute(delete(PageTag).where(PageTag.page_id == page.id))
    for t in tags:
        db.add(PageTag(page_id=page.id, tag=t))

    headings = link_svc.extract_headings(body)
    await _update_fts(db, page, tags, headings, body)
    await _reindex_links_for_page(db, page, body)
    if action == "create":
        await _resolve_dangling_links(db, page)

    # Revision schreiben (oder bei schnellem Sync desselben Autors zusammenfassen)
    last_rev = (await db.scalars(
        select(Revision).where(Revision.page_id == page.id).order_by(Revision.rev_number.desc()).limit(1)
    )).first()
    if (
        coalesce_minutes > 0
        and last_rev
        and last_rev.by == caller.by
        and last_rev.action == "update"
        and (now - last_rev.at) < timedelta(minutes=coalesce_minutes)
    ):
        last_rev.content_full = full_md
        last_rev.title_snapshot = eff_title
        last_rev.path_snapshot = norm_path
        last_rev.added_lines += added
        last_rev.deleted_lines += deleted
        last_rev.at = now
        rev = last_rev
    else:
        next_num = (last_rev.rev_number + 1) if last_rev else 1
        rev = Revision(
            page_id=page.id,
            rev_number=next_num,
            action=action,
            space=space,
            path_snapshot=norm_path,
            title_snapshot=eff_title,
            content_full=full_md,
            added_lines=added,
            deleted_lines=deleted,
            note=(note or "")[:240],
            by=caller.by,
            approved_by=approved_by,
            via=caller.via,
            at=now,
        )
        db.add(rev)
        await db.flush()
    page.current_revision_id = rev.id

    if space == "memory" and norm_path not in memory_guard.SYSTEM_MEMORY_PAGES:
        await memory_guard.rebuild_memory_index(db)

    pending(db).append({
        "type": "changed",
        "revision_id": rev.id,
        "page_id": page.id,
        "path": page.path,
        "space": page.space,
        "action": action,
        "by": caller.by,
    })
    return {
        "page": await page_detail_dict(db, page, caller),
        "revision_id": rev.id,
        "merged": merged,
    }


def apply_patch_to_body(
    body: str,
    *,
    old_text: str | None = None,
    new_text: str | None = None,
    heading: str | None = None,
    mode: str = "replace",
) -> str:
    """Wendet ein gezieltes Patch auf den Markdown-Body an:
    * `old_text` angegeben → ersetzt genau diese Stelle durch `new_text`
    * `heading` angegeben:
        - `mode="append"`  → hängt `new_text` ans Ende dieses Abschnitts (oder legt `## <heading>` neu an)
        - `mode="replace"` → ersetzt den Inhalt dieses Abschnitts durch `new_text`
    * sonst `mode="append"` → hängt `new_text` ans Dokumentende an.
    """
    src = (body or "").replace("\r\n", "\n")
    addition = (new_text or "").replace("\r\n", "\n")

    if old_text is not None:
        needle = old_text.replace("\r\n", "\n")
        if not needle:
            raise Invalid("old_text darf nicht leer sein")
        count = src.count(needle)
        if count == 0:
            raise Invalid("Die zu ersetzende Textstelle (old_text) wurde auf der Seite nicht gefunden.")
        if count > 1:
            raise Invalid("old_text kommt mehrfach auf der Seite vor – bitte etwas mehr Kontext angeben.")
        return src.replace(needle, addition, 1)

    if heading:
        want = heading.strip().lstrip("#").strip().lower()
        lines = src.splitlines()
        start_idx = None
        start_lvl = 2
        end_idx = len(lines)
        for idx, ln in enumerate(lines):
            m = re.match(r"^(#{1,6})\s+(.+?)\s*$", ln)
            if not m:
                continue
            lvl = len(m.group(1))
            txt = m.group(2).strip().lower()
            if start_idx is None and (txt == want or slugify(txt, "") == slugify(want, "_none_")):
                start_idx = idx
                start_lvl = lvl
            elif start_idx is not None and lvl <= start_lvl:
                end_idx = idx
                break
        if start_idx is None:
            clean_h = heading.strip().lstrip("#").strip()
            sep = "\n\n" if src.strip() else ""
            return f"{src.rstrip()}{sep}## {clean_h}\n\n{addition.strip()}\n"
        if mode == "append":
            before_sec = lines[:end_idx]
            while before_sec and not before_sec[-1].strip():
                before_sec.pop()
            after_sec = lines[end_idx:]
            rebuilt = before_sec + ["", addition.strip(), ""] + after_sec
            return "\n".join(rebuilt).strip() + "\n"
        # mode == "replace"
        rebuilt = lines[: start_idx + 1] + ["", addition.strip(), ""] + lines[end_idx:]
        return "\n".join(rebuilt).strip() + "\n"

    # Dokumentende anhängen
    sep = "\n\n" if src.strip() else ""
    return f"{src.rstrip()}{sep}{addition.strip()}\n"


async def patch_page(
    db: AsyncSession,
    caller: Caller,
    ref: str,
    *,
    old_text: str | None = None,
    new_text: str | None = None,
    heading: str | None = None,
    mode: str = "append",
    frontmatter_patch: dict | None = None,
    title: str | None = None,
    note: str = "",
    approved_by: str | None = None,
    approved_proposal: bool = False,
) -> dict:
    page = await get_page_or_404(db, ref, caller)
    perm.require_write(caller, page.space, approved_proposal=approved_proposal)
    raw = read_vault_file(page.path)
    fm, body = parse_markdown(raw)
    if new_text is not None or old_text is not None:
        body = apply_patch_to_body(body, old_text=old_text, new_text=new_text, heading=heading, mode=mode)
    if frontmatter_patch:
        fm.update({k: v for k, v in frontmatter_patch.items() if v is not None})
    return await save_page(
        db,
        caller,
        path=page.path,
        title=title or page.title,
        body=body,
        frontmatter=fm,
        note=note or (f"Abschnitt „{heading}“ aktualisiert" if heading else "Seite aktualisiert"),
        approved_by=approved_by,
        approved_proposal=approved_proposal,
    )


async def move_page(
    db: AsyncSession,
    caller: Caller,
    ref: str,
    new_raw_path: str,
    *,
    new_title: str | None = None,
    approved_by: str | None = None,
    approved_proposal: bool = False,
) -> dict:
    page = await get_page_or_404(db, ref, caller)
    new_path, new_space, new_folder, new_slug = normalize_path(new_raw_path)
    perm.require_write(caller, page.space, approved_proposal=approved_proposal)
    perm.require_write(caller, new_space, approved_proposal=approved_proposal)

    clash = await db.scalar(select(Page).where(func.lower(Page.path) == new_path.lower(), Page.id != page.id, Page.deleted_at.is_(None)))
    if clash:
        raise Invalid(f"Unter `{new_path}` existiert bereits eine Seite.")

    old_path, old_slug = page.path, page.slug
    raw = read_vault_file(old_path)
    fm, body = parse_markdown(raw)
    if new_title:
        fm["title"] = new_title.strip()[:200]
        page.title = fm["title"]

    if new_space == "memory" and new_path not in memory_guard.SYSTEM_MEMORY_PAGES:
        fm = memory_guard.validate_memory_write(caller, new_path, page.title, body, fm, creating=False)

    full_md = compose_markdown(fm, body)
    write_vault_file(new_path, full_md)
    if old_path != new_path:
        delete_vault_file(old_path)

    now = utcnow()
    old_space = page.space
    page.space = new_space
    page.path = new_path
    page.folder = new_folder
    page.slug = new_slug
    page.frontmatter = fm
    page.updated_by = caller.by
    page.updated_at = now

    # Alle eingehenden [[Wikilinks]] automatisch auf den neuen Pfad umschreiben!
    in_links = (await db.scalars(select(PageLink).where(PageLink.target_page_id == page.id))).all()
    touched_pages = 0
    for sid in sorted({lk.source_page_id for lk in in_links if lk.source_page_id != page.id}):
        sp = await db.get(Page, sid)
        if not sp or sp.deleted_at is not None or sp.managed_by == "ida_sync":
            continue
        s_raw = read_vault_file(sp.path)
        s_fm, s_body = parse_markdown(s_raw)
        new_s_body, count = link_svc.rewrite_links_in_body(s_body, old_path, old_slug, new_path)
        if count > 0:
            touched_pages += 1
            s_full = compose_markdown(s_fm, new_s_body)
            write_vault_file(sp.path, s_full)
            sp.body_hash = _sha256(new_s_body)
            sp.updated_at = now
            await _reindex_links_for_page(db, sp, new_s_body)

    tags = link_svc.extract_tags(fm, body)
    headings = link_svc.extract_headings(body)
    await _update_fts(db, page, tags, headings, body)
    await _reindex_links_for_page(db, page, body)

    last_rev = (await db.scalars(
        select(Revision).where(Revision.page_id == page.id).order_by(Revision.rev_number.desc()).limit(1)
    )).first()
    rev = Revision(
        page_id=page.id,
        rev_number=(last_rev.rev_number + 1) if last_rev else 1,
        action="move",
        space=new_space,
        path_snapshot=new_path,
        title_snapshot=page.title,
        content_full=full_md,
        added_lines=0,
        deleted_lines=0,
        note=f"Verschoben von {old_path} nach {new_path}",
        by=caller.by,
        approved_by=approved_by,
        via=caller.via,
        at=now,
    )
    db.add(rev)
    await db.flush()
    page.current_revision_id = rev.id

    if "memory" in (old_space, new_space):
        await memory_guard.rebuild_memory_index(db)

    pending(db).append({
        "type": "changed",
        "revision_id": rev.id,
        "page_id": page.id,
        "path": page.path,
        "old_path": old_path,
        "space": page.space,
        "action": "move",
        "by": caller.by,
    })
    return {
        "page": await page_detail_dict(db, page, caller),
        "revision_id": rev.id,
        "rewritten_backlinks": touched_pages,
    }


async def delete_page(
    db: AsyncSession,
    caller: Caller,
    ref: str,
    *,
    note: str = "",
    approved_by: str | None = None,
    approved_proposal: bool = False,
) -> dict:
    page = await get_page_or_404(db, ref, caller)
    perm.require_write(caller, page.space, approved_proposal=approved_proposal)
    if page.path in memory_guard.SYSTEM_MEMORY_PAGES:
        raise Invalid(f"`{page.path}` ist eine Systemseite und kann nicht gelöscht werden.")

    raw = read_vault_file(page.path)
    delete_vault_file(page.path)
    now = utcnow()
    page.deleted_at = now
    page.updated_by = caller.by
    page.updated_at = now

    await _update_fts(db, page, [], [], "")
    await db.execute(delete(PageLink).where(PageLink.source_page_id == page.id))
    # Eingehende Links bleiben mit target_page_id=None erhalten, damit ein Wiederherstellen sie sofort wieder verknüpft!
    for lk in (await db.scalars(select(PageLink).where(PageLink.target_page_id == page.id))).all():
        lk.target_page_id = None

    last_rev = (await db.scalars(
        select(Revision).where(Revision.page_id == page.id).order_by(Revision.rev_number.desc()).limit(1)
    )).first()
    rev = Revision(
        page_id=page.id,
        rev_number=(last_rev.rev_number + 1) if last_rev else 1,
        action="delete",
        space=page.space,
        path_snapshot=page.path,
        title_snapshot=page.title,
        content_full=raw,
        added_lines=0,
        deleted_lines=len(raw.splitlines()),
        note=(note or "In den Papierkorb verschoben")[:240],
        by=caller.by,
        approved_by=approved_by,
        via=caller.via,
        at=now,
    )
    db.add(rev)
    await db.flush()
    page.current_revision_id = rev.id

    if page.space == "memory":
        await memory_guard.rebuild_memory_index(db)

    pending(db).append({
        "type": "changed",
        "revision_id": rev.id,
        "page_id": page.id,
        "path": page.path,
        "space": page.space,
        "action": "delete",
        "by": caller.by,
    })
    return {"ok": True, "id": page.id, "path": page.path, "title": page.title, "revision_id": rev.id}


async def restore_from_trash(db: AsyncSession, caller: Caller, page_id: str) -> dict:
    page = await db.get(Page, page_id)
    if not page or page.deleted_at is None:
        raise NotFound("Seite liegt nicht im Papierkorb")
    perm.require_write(caller, page.space)
    last_rev = (await db.scalars(
        select(Revision).where(Revision.page_id == page.id).order_by(Revision.rev_number.desc()).limit(1)
    )).first()
    content = last_rev.content_full if last_rev else compose_markdown({"title": page.title}, "")
    write_vault_file(page.path, content)
    fm, body = parse_markdown(content)
    now = utcnow()
    page.deleted_at = None
    page.updated_by = caller.by
    page.updated_at = now
    tags = link_svc.extract_tags(fm, body)
    headings = link_svc.extract_headings(body)
    await _update_fts(db, page, tags, headings, body)
    await _reindex_links_for_page(db, page, body)
    await _resolve_dangling_links(db, page)

    rev = Revision(
        page_id=page.id,
        rev_number=(last_rev.rev_number + 1) if last_rev else 1,
        action="restore",
        space=page.space,
        path_snapshot=page.path,
        title_snapshot=page.title,
        content_full=content,
        note="Aus dem Papierkorb wiederhergestellt",
        by=caller.by,
        via=caller.via,
        at=now,
    )
    db.add(rev)
    await db.flush()
    page.current_revision_id = rev.id
    if page.space == "memory":
        await memory_guard.rebuild_memory_index(db)
    pending(db).append({"type": "changed", "revision_id": rev.id, "page_id": page.id, "path": page.path, "space": page.space, "action": "restore", "by": caller.by})
    return {"page": await page_detail_dict(db, page, caller), "revision_id": rev.id}


async def restore_revision(db: AsyncSession, caller: Caller, page_ref: str, revision_id: int) -> dict:
    """Setzt eine Seite auf den exakten Stand einer früheren Revision zurück (legt eine neue Revision an)."""
    page = await get_page_or_404(db, page_ref, caller)
    perm.require_write(caller, page.space)
    target = await db.get(Revision, revision_id)
    if not target or target.page_id != page.id:
        raise NotFound("Revision gehört nicht zu dieser Seite")
    return await save_page(
        db,
        caller,
        path=page.path,
        raw_markdown=target.content_full,
        note=f"Zurückgesetzt auf Version #{target.rev_number}",
    )


async def undo_revision(db: AsyncSession, caller: Caller, revision_id: int) -> dict:
    """Macht eine konkrete Revision rückgängig (für KI-Undo in `memory/` und den Verlauf-Button)."""
    rev = await db.get(Revision, revision_id)
    if not rev:
        raise NotFound("Revision nicht gefunden")
    page = await db.get(Page, rev.page_id)
    if not page:
        raise NotFound("Zugehörige Seite existiert nicht mehr")
    if caller.kind in ("ida", "mcp") and rev.by != caller.by:
        from ..errors import Forbidden
        raise Forbidden("Agents dürfen nur ihre eigenen Änderungen per Undo zurücknehmen.")
    perm.require_write(caller, page.space)

    if rev.action == "delete":
        return await restore_from_trash(db, caller, page.id)
    if rev.action == "create" and rev.rev_number == 1:
        return await delete_page(db, caller, page.id, note=f"Undo Anlage (#{rev.rev_number})")
    prev = (await db.scalars(
        select(Revision).where(Revision.page_id == page.id, Revision.rev_number < rev.rev_number).order_by(Revision.rev_number.desc()).limit(1)
    )).first()
    if not prev:
        raise Invalid("Keine frühere Version zum Wiederherstellen vorhanden")
    if page.deleted_at is not None:
        await restore_from_trash(db, caller, page.id)
    return await save_page(
        db,
        caller,
        path=prev.path_snapshot,
        raw_markdown=prev.content_full,
        note=f"Rückgängig: Revision #{rev.rev_number}",
    )


async def list_tree(db: AsyncSession, caller: Caller, space: str | None = None) -> dict:
    """Liefert alle lesbaren Seiten + die physische Ordnerstruktur (inkl. noch leerer Lebens-Ordner!)."""
    allowed = [s for s in SPACES if perm.can_read_space(caller, s) and (not space or s == space)]
    if not allowed:
        return {"spaces": [], "folders": [], "pages": []}

    rows = (await db.scalars(
        select(Page).where(Page.space.in_(allowed), Page.deleted_at.is_(None)).order_by(Page.path)
    )).all()
    tag_rows = (await db.execute(
        select(PageTag.page_id, PageTag.tag).join(Page, Page.id == PageTag.page_id).where(Page.space.in_(allowed), Page.deleted_at.is_(None))
    )).all()
    tags_by_page: dict[str, list[str]] = {}
    for pid, tg in tag_rows:
        tags_by_page.setdefault(pid, []).append(tg)

    vdir = get_settings().vault_dir
    folders: set[str] = set()
    for sp in allowed:
        sp_dir = vdir / sp
        if sp_dir.is_dir():
            for d in sp_dir.rglob("*"):
                if d.is_dir() and not d.name.startswith("."):
                    folders.add(d.relative_to(vdir).as_posix())
    for p in rows:
        parts = p.folder.split("/")
        for i in range(2, len(parts) + 1):
            folders.add("/".join(parts[:i]))

    return {
        "spaces": allowed,
        "folders": sorted(folders),
        "pages": [page_summary_dict(p, tags_by_page.get(p.id, [])) for p in rows],
    }


def revision_view(r: Revision) -> dict:
    return {
        "id": r.id,
        "page_id": r.page_id,
        "rev_number": r.rev_number,
        "action": r.action,
        "space": r.space,
        "path": r.path_snapshot,
        "title": r.title_snapshot,
        "added_lines": r.added_lines,
        "deleted_lines": r.deleted_lines,
        "note": r.note,
        "by": r.by,
        "approved_by": r.approved_by,
        "via": r.via,
        "at": r.at.isoformat(),
    }
