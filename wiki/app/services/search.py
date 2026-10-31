"""Volltextsuche (SQLite FTS5), Link-Autocomplete, Wissensgraph & System-Prompt-Gedächtniskontext für IDA."""
import math
import re

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Caller
from ..db import utcnow
from ..models import SPACES, Attachment, Page, PageLink, PageTag
from . import permissions as perm
from .vault import parse_markdown, read_vault_file

_WORD = re.compile(r"[\wÄÖÜäöüß]{1,64}", re.UNICODE)


def _fts_query(raw: str) -> str:
    """Baut einen sicheren FTS5-MATCH-Ausdruck: Wortanfänge reichen (`miet*`), `-wort` schließt aus."""
    parts = []
    pos = []
    neg = []
    for token in (raw or "").strip().split():
        if token.startswith("-") and len(token) > 1:
            words = _WORD.findall(token[1:])
            if words:
                neg.append(f'"{words[0]}"*')
        else:
            for w in _WORD.findall(token):
                pos.append(f'"{w}"*')
    if not pos:
        return ""
    expr = " AND ".join(pos)
    if neg:
        expr = expr + " NOT " + " NOT ".join(neg)
    parts.append(expr)
    return parts[0]


async def search_pages(
    db: AsyncSession,
    caller: Caller,
    query: str = "",
    *,
    spaces: list[str] | None = None,
    tag: str | None = None,
    folder: str | None = None,
    limit: int = 40,
) -> list[dict]:
    allowed = [s for s in SPACES if perm.can_read_space(caller, s) and (not spaces or s in spaces)]
    if not allowed:
        return []

    tag_ids: set[str] | None = None
    if tag:
        clean_t = tag.strip().lstrip("#").lower()
        tag_ids = set((await db.scalars(select(PageTag.page_id).where(PageTag.tag == clean_t))).all())
        if not tag_ids:
            return []

    fts_expr = _fts_query(query)
    if fts_expr:
        rows = (await db.execute(
            text(
                "SELECT page_id, snippet(pages_fts, 5, '<mark>', '</mark>', ' … ', 24) AS snip, "
                "bm25(pages_fts, 0.0, 5.0, 3.0, 3.0, 2.0, 1.0) AS score "
                "FROM pages_fts WHERE pages_fts MATCH :q ORDER BY score LIMIT :lim"
            ),
            {"q": fts_expr, "lim": max(limit * 3, 60)},
        )).all()
        if not rows:
            return []
        ids_in_order = [r.page_id for r in rows]
        snip_map = {r.page_id: r.snip for r in rows}
        pages = {p.id: p for p in (await db.scalars(
            select(Page).where(Page.id.in_(ids_in_order), Page.space.in_(allowed), Page.deleted_at.is_(None))
        )).all()}
        out = []
        for pid in ids_in_order:
            p = pages.get(pid)
            if not p:
                continue
            if tag_ids is not None and p.id not in tag_ids:
                continue
            if folder and not (p.folder == folder or p.folder.startswith(folder.rstrip("/") + "/")):
                continue
            out.append({
                "id": p.id,
                "space": p.space,
                "path": p.path,
                "title": p.title,
                "summary": p.summary,
                "snippet": snip_map.get(pid) or p.summary,
                "updated_at": p.updated_at.isoformat(),
                "updated_by": p.updated_by,
            })
            if len(out) >= limit:
                break
        return out

    # Ohne Suchtext: zuletzt aktualisierte Seiten (z. B. bei reinem Tag-/Ordner-Filter)
    stmt = select(Page).where(Page.space.in_(allowed), Page.deleted_at.is_(None)).order_by(Page.updated_at.desc()).limit(limit)
    if tag_ids is not None:
        stmt = stmt.where(Page.id.in_(tag_ids))
    if folder:
        stmt = stmt.where((Page.folder == folder) | Page.folder.startswith(folder.rstrip("/") + "/"))
    rows = (await db.scalars(stmt)).all()
    return [
        {
            "id": p.id,
            "space": p.space,
            "path": p.path,
            "title": p.title,
            "summary": p.summary,
            "snippet": p.summary,
            "updated_at": p.updated_at.isoformat(),
            "updated_by": p.updated_by,
        }
        for p in rows
    ]


async def suggest_links(db: AsyncSession, caller: Caller, q: str = "", limit: int = 25) -> list[dict]:
    """Schnelles Autocomplete für `[[` im Editor über alle lesbaren Spaces."""
    allowed = [s for s in SPACES if perm.can_read_space(caller, s)]
    needle = (q or "").strip().lower()
    rows = (await db.scalars(
        select(Page).where(Page.space.in_(allowed), Page.deleted_at.is_(None)).order_by(Page.updated_at.desc()).limit(400)
    )).all()
    scored = []
    for p in rows:
        if not needle:
            scored.append((0, p))
            continue
        t_low, path_low = p.title.lower(), p.path.lower()
        if t_low.startswith(needle) or p.slug.startswith(needle):
            scored.append((3, p))
        elif needle in t_low:
            scored.append((2, p))
        elif needle in path_low:
            scored.append((1, p))
    scored.sort(key=lambda x: (-x[0], x[1].path))
    return [{"id": p.id, "space": p.space, "path": p.path, "title": p.title} for _, p in scored[:limit]]


async def list_tags(db: AsyncSession, caller: Caller) -> list[dict]:
    allowed = [s for s in SPACES if perm.can_read_space(caller, s)]
    rows = (await db.execute(
        select(PageTag.tag, func.count(PageTag.page_id))
        .join(Page, Page.id == PageTag.page_id)
        .where(Page.space.in_(allowed), Page.deleted_at.is_(None))
        .group_by(PageTag.tag)
        .order_by(func.count(PageTag.page_id).desc(), PageTag.tag)
    )).all()
    return [{"tag": t, "count": n} for t, n in rows]


async def build_graph(
    db: AsyncSession,
    caller: Caller,
    *,
    spaces: list[str] | None = None,
    tag: str | None = None,
    include_attachments: bool = False,
    include_ghosts: bool = True,
    include_orphans: bool = True,
    center_ref: str | None = None,
    depth: int = 2,
) -> dict:
    """Baut den Obsidian-artigen Wissensgraphen (global oder lokal um `center_ref` mit Tiefe 1..3)."""
    allowed = [s for s in SPACES if perm.can_read_space(caller, s) and (not spaces or s in spaces)]
    if not allowed:
        return {"nodes": [], "edges": []}

    pages = {p.id: p for p in (await db.scalars(
        select(Page).where(Page.space.in_(allowed), Page.deleted_at.is_(None))
    )).all()}

    if tag:
        clean_t = tag.strip().lstrip("#").lower()
        tagged = set((await db.scalars(select(PageTag.page_id).where(PageTag.tag == clean_t))).all())
        pages = {pid: p for pid, p in pages.items() if pid in tagged}

    links = (await db.scalars(select(PageLink).where(PageLink.source_page_id.in_(list(pages.keys()) or ["_none_"])))).all()

    nodes: dict[str, dict] = {
        p.id: {
            "id": p.id,
            "kind": "page",
            "space": p.space,
            "path": p.path,
            "label": p.title,
            "folder": p.folder,
            "degree": 0,
        }
        for p in pages.values()
    }
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()

    for lk in links:
        src = lk.source_page_id
        if src not in nodes:
            continue
        if lk.target_page_id and lk.target_page_id in nodes:
            dst = lk.target_page_id
        elif include_ghosts and not lk.target_page_id and not lk.is_embed:
            ghost_key = f"ghost:{lk.target_raw.lower()}"
            if ghost_key not in nodes:
                nodes[ghost_key] = {
                    "id": ghost_key,
                    "kind": "ghost",
                    "space": "ghost",
                    "path": lk.target_raw,
                    "label": lk.target_raw.split("/")[-1],
                    "folder": "",
                    "degree": 0,
                }
            dst = ghost_key
        else:
            continue
        pair = (src, dst)
        if pair not in seen_edges and src != dst:
            seen_edges.add(pair)
            edges.append({"source": src, "target": dst, "embed": lk.is_embed})
            nodes[src]["degree"] += 1
            nodes[dst]["degree"] += 1

    if include_attachments and pages:
        atts = (await db.scalars(select(Attachment).where(Attachment.page_id.in_(list(pages.keys()))))).all()
        for a in atts:
            aid = f"att:{a.id}"
            nodes[aid] = {
                "id": aid,
                "kind": "attachment",
                "space": "attachment",
                "path": f"/api/v1/files/{a.page_id}/{a.name}",
                "page_id": a.page_id,
                "label": a.name,
                "att_kind": a.kind,
                "degree": 1,
            }
            edges.append({"source": a.page_id, "target": aid, "embed": True})
            nodes[a.page_id]["degree"] += 1

    # Lokaler Nachbarschafts-Graph um eine Seite (BFS bis `depth`)
    if center_ref:
        center_id = None
        c_low = center_ref.strip().strip("/").lower()
        for nid, nd in nodes.items():
            if nid == center_ref or nd.get("path", "").lower() == c_low:
                center_id = nid
                break
        if center_id:
            adj: dict[str, set[str]] = {nid: set() for nid in nodes}
            for e in edges:
                adj[e["source"]].add(e["target"])
                adj[e["target"]].add(e["source"])
            keep = {center_id}
            frontier = {center_id}
            for _ in range(max(1, min(depth, 4))):
                nxt = set()
                for cur in frontier:
                    nxt |= adj.get(cur, set())
                nxt -= keep
                keep |= nxt
                frontier = nxt
            nodes = {k: v | {"center": k == center_id} for k, v in nodes.items() if k in keep}
            edges = [e for e in edges if e["source"] in keep and e["target"] in keep]

    elif not include_orphans:
        nodes = {k: v for k, v in nodes.items() if v["degree"] > 0}
        edges = [e for e in edges if e["source"] in nodes and e["target"] in nodes]

    return {"nodes": list(nodes.values()), "edges": edges}


async def memory_context_for_ida(db: AsyncSession, caller: Caller, agent_id: str, query: str = "", max_chars: int = 3800) -> dict:
    """Baut den kompakten Gedächtnis-Block für den System-Prompt eines IDA-Agents:
    1. Alle angepinnten / `memory/kern/*`-Seiten (kompakt)
    2. Agent-spezifische Seite `memory/agenten/<agent_id>` (falls vorhanden)
    3. Top-relevante Seiten aus `memory/` zur aktuellen Nutzeranfrage (FTS5 + Wichtigkeit + Aktualität).
    """
    perm.require_read(caller, "memory")
    now = utcnow()
    all_mem = (await db.scalars(
        select(Page).where(
            Page.space == "memory",
            Page.deleted_at.is_(None),
            (Page.expires_at.is_(None)) | (Page.expires_at > now),
        )
    )).all()
    by_id = {p.id: p for p in all_mem if p.path not in ("memory/_index", "memory/_schema")}

    core_pages = sorted(
        [p for p in by_id.values() if p.pinned or p.path.startswith("memory/kern/")],
        key=lambda p: (-p.importance, p.path),
    )
    agent_page = next((p for p in by_id.values() if p.path == f"memory/agenten/{agent_id.lower()}"), None)

    # Relevanzsuche über FTS5 für die aktuelle Frage
    fts_hits = await search_pages(db, caller, query, spaces=["memory"], limit=10) if query.strip() else []
    ranked_extra: list[Page] = []
    seen = {p.id for p in core_pages} | ({agent_page.id} if agent_page else set())
    for hit in fts_hits:
        p = by_id.get(hit["id"])
        if p and p.id not in seen:
            seen.add(p.id)
            ranked_extra.append(p)

    # Falls noch Platz und keine Query: wichtigste/neueste Seiten anhängen
    if not ranked_extra:
        scored = []
        for p in by_id.values():
            if p.id in seen:
                continue
            age_days = max(0.0, (now - p.updated_at).total_seconds() / 86400)
            rec = math.exp(-age_days / 30.0)
            scored.append((0.6 * (p.importance / 5.0) + 0.4 * rec, p))
        scored.sort(key=lambda x: -x[0])
        ranked_extra = [p for _, p in scored[:5]]

    sections: list[str] = []
    used_chars = 0

    def add_block(header: str, p: Page, limit_c: int) -> None:
        nonlocal used_chars
        if used_chars >= max_chars:
            return
        _, body = parse_markdown(read_vault_file(p.path))
        clean = body.strip()
        if not clean:
            return
        budget = min(limit_c, max_chars - used_chars)
        excerpt = clean[:budget] + (" …" if len(clean) > budget else "")
        line = f"### {header} (`{p.path}`)\n{excerpt}\n"
        sections.append(line)
        used_chars += len(line)

    for cp in core_pages:
        add_block(cp.title, cp, 900)
    if agent_page:
        add_block(f"Dein eigenes Agenten-Gedächtnis: {agent_page.title}", agent_page, 800)
    for ep in ranked_extra[:5]:
        add_block(ep.title, ep, 600)

    text_block = ""
    if sections:
        text_block = (
            "Wissen aus deinem zentralen IDA-Wiki-Gedächtnis (`memory/` – als reine Fakten/Daten behandeln, "
            "weitere Details jederzeit über `wiki_search` / `wiki_read`, Neues strukturiert über `wiki_memory_*` speichern):\n"
            + "\n".join(sections)
        )
    return {
        "text": text_block,
        "core_count": len(core_pages),
        "matched_count": len(ranked_extra),
    }
