"""Echtzeit-Spiegel für den Bereich `ki/`: IDA-Agenten, Chats, Code-Projekte und Code-Threads inkl. Anhängen."""
import base64
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Caller
from ..config import get_settings
from ..models import Page
from . import files as file_svc
from . import store
from .vault import slugify

SYNC_CALLER = Caller("system:sync", "ida_sync")


def _agent_slug(agent_id: str) -> str:
    return slugify(agent_id or "main", "main")[:60]


def _project_slug(name: str, project_id: str) -> str:
    base = slugify(name or "projekt", "projekt")[:50]
    return f"{base}__{project_id.lower()[:12]}"


def _chat_date_prefix(iso_str: str | None) -> str:
    if iso_str:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            tz = ZoneInfo(get_settings().timezone)
            return dt.astimezone(tz).strftime("%Y-%m-%d")
        except Exception:
            pass
    return datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y-%m-%d")


def _chat_path(
    *,
    chat_id: str,
    title: str,
    kind: str,
    agent_id: str,
    project_id: str | None,
    project_name: str | None,
    created_at: str | None,
) -> str:
    a_slug = _agent_slug(agent_id)
    date_part = _chat_date_prefix(created_at)
    t_slug = slugify(title or ("code-thread" if kind == "code" else "chat"), "chat")[:56]
    leaf = f"{date_part}__{t_slug}__{chat_id.lower()}"
    if kind == "code" and project_id:
        p_slug = _project_slug(project_name or project_id, project_id)
        return f"ki/{a_slug}/code/{p_slug}/{leaf}"
    return f"ki/{a_slug}/chats/{leaf}"


async def _find_ki_page_by_meta(db: AsyncSession, key: str, value: str) -> Page | None:
    rows = (await db.scalars(select(Page).where(Page.space == "ki", Page.deleted_at.is_(None)))).all()
    for p in rows:
        if (p.frontmatter or {}).get(key) == value:
            return p
    return None


async def sync_state(db: AsyncSession) -> dict:
    """Liefert den aktuellen Spiegel-Stand in `ki/`, damit IDA beim Start nur fehlende/geänderte Objekte nachsynchronisiert."""
    rows = (await db.scalars(select(Page).where(Page.space == "ki", Page.deleted_at.is_(None)))).all()
    agents: dict[str, str] = {}
    projects: dict[str, str] = {}
    chats: dict[str, dict] = {}
    for p in rows:
        fm = p.frontmatter or {}
        if fm.get("entity") == "agent" and fm.get("agent_id"):
            agents[str(fm["agent_id"])] = str(fm.get("source_updated_at") or "")
        elif fm.get("entity") == "code_project" and fm.get("project_id"):
            projects[str(fm["project_id"])] = str(fm.get("source_updated_at") or "")
        elif fm.get("entity") in ("chat", "code_thread") and fm.get("chat_id"):
            chats[str(fm["chat_id"])] = {
                "updated_at": str(fm.get("source_updated_at") or ""),
                "message_count": int(fm.get("message_count") or 0),
                "path": p.path,
            }
    return {"agents": agents, "projects": projects, "chats": chats}


async def sync_agent(db: AsyncSession, data: dict) -> dict:
    agent_id = str(data.get("id") or "main").strip()
    name = str(data.get("name") or agent_id).strip()
    a_slug = _agent_slug(agent_id)
    path = f"ki/{a_slug}/_agent"

    role = str(data.get("role") or data.get("description") or "").strip()
    model = str(data.get("model") or "").strip()
    tools = data.get("tools") or []
    active = bool(data.get("active", True))
    ida_url = str(data.get("ida_url") or "").strip()

    sub_pages = (await db.scalars(
        select(Page)
        .where(Page.space == "ki", Page.deleted_at.is_(None), Page.path.startswith(f"ki/{a_slug}/"))
        .order_by(Page.updated_at.desc())
    )).all()
    chat_links = []
    proj_links = []
    for sp in sub_pages:
        if sp.path == path:
            continue
        fm = sp.frontmatter or {}
        if fm.get("entity") == "code_project":
            proj_links.append(f"- [[{sp.path}|{sp.title}]]")
        elif fm.get("entity") in ("chat", "code_thread"):
            kind_badge = " *(Code)*" if fm.get("entity") == "code_thread" else ""
            chat_links.append(f"- [[{sp.path}|{sp.title}]]{kind_badge}")

    lines = [
        f"# {name}",
        "",
        "> [!info] Automatisch gespiegelter IDA-Agent",
        f"> Agent-ID: `{agent_id}` · Status: **{'Aktiv' if active else 'Pausiert'}**"
        + (f" · Modell: `{model}`" if model else "")
        + (f" · [In IDA öffnen]({ida_url})" if ida_url else ""),
        "",
    ]
    if role:
        lines.extend(["## Rolle & Aufgabe", "", role, ""])
    if tools:
        lines.extend(["## Aktivierte Werkzeuge", "", ", ".join(f"`{t}`" for t in tools), ""])
    if proj_links:
        lines.extend(["## Code-Projekte", "", *proj_links[:30], ""])
    lines.extend(["## Letzte Unterhaltungen & Code-Sessions", ""])
    if chat_links:
        lines.extend(chat_links[:50])
    else:
        lines.append("- *(noch keine gespiegelten Chats)*")
    lines.append("")

    fm = {
        "entity": "agent",
        "agent_id": agent_id,
        "title": f"Agent: {name}",
        "summary": role[:200] or f"IDA-Agent {name}",
        "tags": ["ki-agent", a_slug],
        "source_updated_at": str(data.get("updated_at") or ""),
    }
    res = await store.save_page(
        db,
        SYNC_CALLER,
        path=path,
        title=f"Agent: {name}",
        body="\n".join(lines),
        frontmatter=fm,
        note=f"Agent „{name}“ synchronisiert",
        managed_by="ida_sync",
        coalesce_minutes=30,
    )
    await rebuild_ki_overview(db)
    return res


async def delete_agent(db: AsyncSession, agent_id: str) -> dict:
    a_slug = _agent_slug(agent_id)
    pages = (await db.scalars(
        select(Page).where(Page.space == "ki", Page.deleted_at.is_(None), Page.path.startswith(f"ki/{a_slug}/"))
    )).all()
    deleted = 0
    for p in pages:
        await store.delete_page(db, SYNC_CALLER, p.id, note=f"Agent {agent_id} in IDA gelöscht")
        deleted += 1
    await rebuild_ki_overview(db)
    return {"ok": True, "deleted": deleted}


async def sync_project(db: AsyncSession, data: dict) -> dict:
    project_id = str(data.get("id") or "").strip()
    name = str(data.get("name") or project_id or "Code-Projekt").strip()
    agent_id = str(data.get("agent_id") or "main").strip()
    a_slug = _agent_slug(agent_id)
    p_slug = _project_slug(name, project_id)
    folder_prefix = f"ki/{a_slug}/code/{p_slug}"
    path = f"{folder_prefix}/_projekt"

    existing = await _find_ki_page_by_meta(db, "project_id", project_id)
    if existing and existing.path != path:
        await store.move_page(db, SYNC_CALLER, existing.id, path, new_title=f"Code-Projekt: {name}")

    thread_pages = (await db.scalars(
        select(Page)
        .where(Page.space == "ki", Page.deleted_at.is_(None), Page.path.startswith(f"{folder_prefix}/"))
        .order_by(Page.updated_at.desc())
    )).all()
    thread_links = [f"- [[{tp.path}|{tp.title}]]" for tp in thread_pages if tp.path != path]

    branch = str(data.get("git_branch") or data.get("branch") or "").strip()
    remote = str(data.get("git_remote") or data.get("repo_url") or "").strip()
    desc = str(data.get("description") or "").strip()
    ida_url = str(data.get("ida_url") or "").strip()

    lines = [
        f"# Code-Projekt: {name}",
        "",
        "> [!tip] Gespiegeltes IDA-Code-Projekt",
        f"> Gehört zu [[ki/{a_slug}/_agent|Agent {agent_id}]]"
        + (f" · Branch: `{branch}`" if branch else "")
        + (f" · Remote: `{remote}`" if remote else "")
        + (f" · [Im IDA Code-Bereich öffnen]({ida_url})" if ida_url else ""),
        "",
    ]
    if desc:
        lines.extend(["## Beschreibung", "", desc, ""])
    lines.extend(["## Code-Threads in diesem Projekt", ""])
    if thread_links:
        lines.extend(thread_links)
    else:
        lines.append("- *(noch keine Code-Threads)*")
    lines.append("")

    fm = {
        "entity": "code_project",
        "project_id": project_id,
        "agent_id": agent_id,
        "title": f"Code-Projekt: {name}",
        "summary": desc[:200] or f"Code-Projekt {name}",
        "tags": ["ki-code", "projekt", slugify(name, "projekt")[:40]],
        "source_updated_at": str(data.get("updated_at") or ""),
    }
    res = await store.save_page(
        db,
        SYNC_CALLER,
        path=path,
        title=f"Code-Projekt: {name}",
        body="\n".join(lines),
        frontmatter=fm,
        note=f"Code-Projekt „{name}“ synchronisiert",
        managed_by="ida_sync",
        coalesce_minutes=30,
    )
    await rebuild_ki_overview(db)
    return res


async def delete_project(db: AsyncSession, project_id: str) -> dict:
    existing = await _find_ki_page_by_meta(db, "project_id", project_id)
    deleted = 0
    if existing:
        prefix = existing.folder + "/"
        pages = (await db.scalars(
            select(Page).where(Page.space == "ki", Page.deleted_at.is_(None), Page.path.startswith(prefix))
        )).all()
        for p in pages:
            await store.delete_page(db, SYNC_CALLER, p.id, note=f"Code-Projekt {project_id} gelöscht")
            deleted += 1
    await rebuild_ki_overview(db)
    return {"ok": True, "deleted": deleted}


def _render_tool_calls(tool_calls: list[dict]) -> list[str]:
    out: list[str] = []
    for tc in tool_calls:
        tname = str(tc.get("name") or tc.get("tool") or "tool")
        args = tc.get("args") or tc.get("arguments") or {}
        result = tc.get("result") or tc.get("output") or ""
        out.append(f"<details><summary>🔧 Werkzeug: <code>{tname}</code></summary>")
        out.append("")
        if args:
            pretty_args = json.dumps(args, ensure_ascii=False, indent=2) if not isinstance(args, str) else args
            out.append("```json")
            out.append(pretty_args[:2000])
            out.append("```")
        if result:
            res_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
            fence = "diff" if ("--- " in res_str and "+++ " in res_str) else "text"
            out.append(f"```{fence}")
            out.append(res_str[:3000])
            out.append("```")
        out.append("</details>")
        out.append("")
    return out


async def sync_chat(db: AsyncSession, data: dict) -> dict:
    chat_id = str(data.get("id") or "").strip()
    kind = "code" if str(data.get("kind") or "chat") == "code" else "chat"
    agent_id = str(data.get("agent_id") or "main").strip()
    agent_name = str(data.get("agent_name") or agent_id).strip()
    project_id = str(data.get("project_id") or "").strip() or None
    project_name = str(data.get("project_name") or "").strip() or None
    title = str(data.get("title") or ("Code-Session" if kind == "code" else "Unterhaltung")).strip()
    created_at = str(data.get("created_at") or "")
    updated_at = str(data.get("updated_at") or "")
    ida_url = str(data.get("ida_url") or "").strip()
    messages = data.get("messages") or []
    files = data.get("files") or []

    target_path = _chat_path(
        chat_id=chat_id,
        title=title,
        kind=kind,
        agent_id=agent_id,
        project_id=project_id,
        project_name=project_name,
        created_at=created_at,
    )

    existing = await _find_ki_page_by_meta(db, "chat_id", chat_id)
    if existing and existing.path != target_path:
        await store.move_page(db, SYNC_CALLER, existing.id, target_path, new_title=title)

    a_slug = _agent_slug(agent_id)
    header_links = [f"[[ki/{a_slug}/_agent|Agent: {agent_name}]]"]
    if kind == "code" and project_id:
        p_slug = _project_slug(project_name or project_id, project_id)
        header_links.append(f"[[ki/{a_slug}/code/{p_slug}/_projekt|Projekt: {project_name or project_id}]]")
    if ida_url:
        header_links.append(f"[Direkt in IDA öffnen]({ida_url})")

    callout_type = "tip" if kind == "code" else "note"
    callout_label = "Gespiegelter IDA Code-Thread" if kind == "code" else "Gespiegelter IDA Chat"

    lines = [
        f"# {title}",
        "",
        f"> [!{callout_type}] {callout_label}",
        f"> {' · '.join(header_links)} · {len(messages)} Nachricht{'en' if len(messages) != 1 else ''}",
        "",
    ]

    files_by_msg: dict[str, list[dict]] = {}
    loose_files: list[dict] = []
    for f in files:
        mid = str(f.get("message_id") or "")
        if mid:
            files_by_msg.setdefault(mid, []).append(f)
        else:
            loose_files.append(f)

    for msg in messages:
        role = str(msg.get("role") or "user")
        if role == "system":
            continue
        who = "👤 **Du**" if role == "user" else f"🤖 **{agent_name}**"
        ts = str(msg.get("created_at") or "")[:16].replace("T", " ")
        lines.append(f"### {who}" + (f" · `{ts}`" if ts else ""))
        lines.append("")
        content = str(msg.get("content") or "").strip()
        if content:
            lines.append(content)
            lines.append("")
        tcs = msg.get("tool_calls") or []
        if tcs:
            lines.extend(_render_tool_calls(tcs))
        mid = str(msg.get("id") or "")
        for mf in files_by_msg.get(mid, []):
            fname = file_svc.safe_filename(str(mf.get("name") or "datei"))
            lines.append(f"![[{fname}]]")
            lines.append("")

    if loose_files:
        lines.extend(["## Angehängte Dateien", ""])
        for lf in loose_files:
            fname = file_svc.safe_filename(str(lf.get("name") or "datei"))
            lines.append(f"![[{fname}]]")
            lines.append("")

    tags = ["ki-code", a_slug] if kind == "code" else ["ki-chat", a_slug]
    if project_name:
        tags.append(slugify(project_name, "projekt")[:40])

    fm = {
        "entity": "code_thread" if kind == "code" else "chat",
        "chat_id": chat_id,
        "kind": kind,
        "agent_id": agent_id,
        "agent_name": agent_name,
        **({"project_id": project_id, "project_name": project_name} if project_id else {}),
        "message_count": len(messages),
        "pinned": bool(data.get("pinned", False)),
        "ida_url": ida_url,
        "tags": tags,
        "source_updated_at": updated_at,
    }

    res = await store.save_page(
        db,
        SYNC_CALLER,
        path=target_path,
        title=title,
        body="\n".join(lines),
        frontmatter=fm,
        note=f"{'Code-Thread' if kind == 'code' else 'Chat'} „{title}“ synchronisiert",
        managed_by="ida_sync",
        coalesce_minutes=15,
    )
    page_id = res["page"]["id"]

    uploaded_any = False
    for f in files:
        b64 = f.get("data_b64")
        if not b64:
            continue
        raw_bytes = base64.b64decode(b64)
        await file_svc.upload_attachment(
            db,
            SYNC_CALLER,
            page_id,
            str(f.get("name") or "datei"),
            str(f.get("mime") or "application/octet-stream"),
            raw_bytes,
            source_chat_file_id=str(f.get("id") or "")[:12] or None,
            log_revision=False,
        )
        uploaded_any = True

    if uploaded_any:
        pg_obj = await db.get(Page, page_id)
        if pg_obj:
            res["page"] = await store.page_detail_dict(db, pg_obj, SYNC_CALLER)

    agent_page = await db.scalar(select(Page).where(Page.path == f"ki/{a_slug}/_agent", Page.deleted_at.is_(None)))
    if not agent_page:
        await sync_agent(db, {"id": agent_id, "name": agent_name})
    if kind == "code" and project_id:
        proj_page = await _find_ki_page_by_meta(db, "project_id", project_id)
        if not proj_page:
            await sync_project(db, {"id": project_id, "name": project_name or project_id, "agent_id": agent_id})

    await rebuild_ki_overview(db)
    return res


async def delete_chat(db: AsyncSession, chat_id: str) -> dict:
    existing = await _find_ki_page_by_meta(db, "chat_id", chat_id)
    if not existing:
        return {"ok": True, "deleted": False}
    await store.delete_page(db, SYNC_CALLER, existing.id, note=f"Chat {chat_id} in IDA gelöscht")
    await rebuild_ki_overview(db)
    return {"ok": True, "deleted": True}


async def rebuild_ki_overview(db: AsyncSession) -> dict:
    """Pflegt `ki/_uebersicht.md` mit allen gespiegelten Agenten, Code-Projekten und aktuellen Unterhaltungen."""
    rows = (await db.scalars(
        select(Page).where(Page.space == "ki", Page.deleted_at.is_(None)).order_by(Page.updated_at.desc())
    )).all()
    agents = [p for p in rows if (p.frontmatter or {}).get("entity") == "agent"]
    projects = [p for p in rows if (p.frontmatter or {}).get("entity") == "code_project"]
    chats = [p for p in rows if (p.frontmatter or {}).get("entity") == "chat"]
    codes = [p for p in rows if (p.frontmatter or {}).get("entity") == "code_thread"]

    lines = [
        "# KI & Chats – Automatischer IDA-Spiegel",
        "",
        "> [!note] Live-Archiv aus IDA",
        "> Alle deine IDA-Agenten, Unterhaltungen, Code-Projekte und Code-Threads (inklusive Bildern, PDFs und Dateien) "
        "werden hier automatisch als durchsuchbare und verlinkbare Markdown-Seiten gespiegelt. "
        "Du kannst aus `privat/` und `memory/` jederzeit per `[[ki/...]]` auf Chats und Code-Sessions verweisen.",
        "",
        "## Agenten",
        "",
    ]
    if agents:
        for a in sorted(agents, key=lambda x: x.title):
            lines.append(f"- [[{a.path}|{a.title}]]" + (f" — {a.summary}" if a.summary else ""))
    else:
        lines.append("- *(werden automatisch beim Start von IDA synchronisiert)*")
    lines.append("")

    lines.extend(["## Code-Projekte & Code-Threads", ""])
    if projects:
        for pr in sorted(projects, key=lambda x: x.title):
            lines.append(f"- [[{pr.path}|{pr.title}]]" + (f" — {pr.summary}" if pr.summary else ""))
    if codes:
        lines.append("")
        lines.append("### Letzte Code-Sessions")
        lines.append("")
        for c in codes[:25]:
            lines.append(f"- [[{c.path}|{c.title}]]")
    elif not projects:
        lines.append("- *(noch keine Code-Projekte oder Code-Threads vorhanden)*")
    lines.append("")

    lines.extend(["## Letzte Unterhaltungen", ""])
    if chats:
        for ch in chats[:30]:
            lines.append(f"- [[{ch.path}|{ch.title}]]")
    else:
        lines.append("- *(noch keine gespiegelten Chats)*")
    lines.append("")

    return await store.save_page(
        db,
        SYNC_CALLER,
        path="ki/_uebersicht",
        title="KI & Chats – Übersicht",
        body="\n".join(lines),
        frontmatter={
            "entity": "overview",
            "title": "KI & Chats – Übersicht",
            "summary": "Zentrale Übersicht aller gespiegelten IDA-Agenten, Chats und Code-Projekte",
            "tags": ["ki-archiv"],
        },
        note="KI-Übersicht aktualisiert",
        managed_by="ida_sync",
        coalesce_minutes=60,
    )
