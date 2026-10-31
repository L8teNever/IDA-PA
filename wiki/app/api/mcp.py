"""Externer MCP-Server unter `/mcp` (Streamable HTTP JSON-RPC 2.0, Protokoll 2025-03-26).

Sicherheitsmodell für externe KI-Clients (Claude Code, Cursor, Claude Desktop, VS Code usw.):
* Bearer-Token zwingend erforderlich (`WIKI_MCP_TOKEN` oder benanntes Token `wk_...` aus den Wiki-Einstellungen)
* `memory/` (gemeinsames KI-Gedächtnis): Lesen + strukturiertes Pflegen erlaubt (Kategorie-Zwang, Geheimnis-Scanner, Auto-Index)
* `privat/` (persönliches Wiki): STRIKT NUR LESEN (sofern für dieses Token freigegeben) – NIEMALS Schreibzugriff!
* `ki/` (gespiegelte IDA-Chats & Code-Projekte): STRIKT NUR LESEN (sofern für dieses Token freigegeben).
"""
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, update

from ..auth import Caller, caller, hash_token
from ..db import session_scope, utcnow
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..models import McpToken, Revision
from ..services import permissions as perm, search as search_svc, store
from ..services.vault import normalize_path, read_vault_file

router = APIRouter(tags=["mcp"])

MCP_INSTRUCTIONS = (
    "IDA Wiki MCP-Server mit 3 streng getrennten Hauptbereichen:\n"
    "1. `memory/` – Gemeinsames strukturiertes Langzeitgedächtnis aller KI-Agenten. "
    "Lies zuerst `wiki_memory_index` oder suche per `wiki_search`, bevor du schreibst. "
    "Pflege bestehende Dossier-Seiten bevorzugt mit `wiki_memory_patch` weiter, statt lose Einzelnotizen anzulegen. "
    "Erlaubte Kategorien unter `memory/`: kern, personen, projekte, konzepte-wissen, entscheidungen, agenten, journal.\n"
    "2. `privat/` – Persönliches Lebens-Wiki des Nutzers (STRIKT NUR LESEN über MCP).\n"
    "3. `ki/` – Automatisch gespiegelte IDA-Chats, Agenten & Code-Projekte (STRIKT NUR LESEN)."
)

MCP_TOOLS = [
    {
        "name": "wiki_memory_index",
        "description": "Liest den zentralen Katalog `memory/_index` aller Seiten im gemeinsamen KI-Gedächtnis sowie Kurzinfos zu den erlaubten Kategorien.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "wiki_search",
        "description": "Volltext- und Schlagwortsuche über das Wiki (`memory/`, sowie lesend `privat/` und `ki/`).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriffe (Wortanfänge genügen, `-wort` schließt aus)"},
                "space": {"type": "string", "enum": ["memory", "privat", "ki"], "description": "Optional auf einen Bereich eingrenzen"},
                "tag": {"type": "string", "description": "Optional nach #tag filtern"},
                "folder": {"type": "string", "description": "Optional auf Unterordner eingrenzen, z. B. `memory/projekte`"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "wiki_read",
        "description": "Liest eine Wiki-Seite vollständig inklusive Frontmatter, Markdown-Inhalt, ausgehenden Links, Backlinks und Dateianhängen.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Seitenpfad (z. B. `memory/projekte/ida` oder `privat/_start`) oder 12-stellige Seiten-ID"},
            },
        },
    },
    {
        "name": "wiki_list",
        "description": "Listet Ordner und Seiten eines Bereichs auf.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space": {"type": "string", "enum": ["memory", "privat", "ki"]},
                "folder": {"type": "string", "description": "Optionaler Ordnerfilter, z. B. `privat/04-arbeit-projekte`"},
            },
        },
    },
    {
        "name": "wiki_backlinks",
        "description": "Liefert alle eingehenden Backlinks und ausgehenden `[[Wikilinks]]` einer Seite.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
    },
    {
        "name": "wiki_recent_changes",
        "description": "Zeigt die letzten Änderungen im Wiki-Versionsverlauf.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space": {"type": "string", "enum": ["memory", "privat", "ki"]},
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "wiki_memory_save",
        "description": (
            "Erstellt oder aktualisiert eine strukturierte Wissensseite im gemeinsamen KI-Gedächtnis `memory/<kategorie>/<slug>`. "
            "Erlaubte Kategorien: kern, personen, projekte, konzepte-wissen, entscheidungen, agenten, journal. "
            "Prüfe vorher per `wiki_search` oder `wiki_memory_index`, ob bereits eine passende Seite existiert!"
        ),
        "inputSchema": {
            "type": "object",
            "required": ["category", "slug", "title", "summary", "body"],
            "properties": {
                "category": {
                    "type": "string",
                    "enum": list(perm.MEMORY_CATEGORIES),
                    "description": "Feste Gedächtnis-Kategorie unter `memory/`",
                },
                "slug": {"type": "string", "description": "Sprechender Dateiname ohne `.md`, z. B. `ida-architektur`"},
                "title": {"type": "string", "description": "Klarer Seitentitel"},
                "summary": {"type": "string", "description": "Prägnante 1-Zeilen-Zusammenfassung für `memory/_index`"},
                "body": {"type": "string", "description": "Strukturierter Markdown-Inhalt (nutze `##`-Abschnitte und `[[Wikilinks]]`)"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "importance": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                "pinned": {"type": "boolean", "description": "Nur in `kern` erlaubt: immer im System-Prompt mitladen"},
                "expires_in_days": {"type": "integer", "description": "Optional für temporäre Notizen in `journal`"},
                "note": {"type": "string", "description": "Kurze Änderungsnotiz für die Wiki-History"},
                "force_new": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "wiki_memory_patch",
        "description": "Aktualisiert gezielt einen Abschnitt oder Textblock einer bestehenden Seite in `memory/` (bevorzugt gegenüber Komplett-Überschreiben).",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Pfad in `memory/`, z. B. `memory/projekte/ida`"},
                "heading": {"type": "string", "description": "Überschrift des Abschnitts (wird neu angelegt, falls noch nicht vorhanden)"},
                "mode": {"type": "string", "enum": ["append", "replace"], "default": "append"},
                "old_text": {"type": "string", "description": "Exakte Textstelle zum Ersetzen (alternativ zu `heading`)"},
                "new_text": {"type": "string", "description": "Neuer bzw. anzuhängender Markdown-Inhalt"},
                "summary": {"type": "string", "description": "Optional aktualisierte Kurz-Zusammenfassung"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "note": {"type": "string"},
            },
        },
    },
    {
        "name": "wiki_memory_move",
        "description": "Verschiebt oder benennt eine Seite innerhalb von `memory/` um und schreibt alle eingehenden `[[Wikilinks]]` automatisch um.",
        "inputSchema": {
            "type": "object",
            "required": ["from_path", "to_path"],
            "properties": {
                "from_path": {"type": "string"},
                "to_path": {"type": "string"},
                "title": {"type": "string"},
            },
        },
    },
    {
        "name": "wiki_memory_delete",
        "description": "Verschiebt eine veraltete Seite in `memory/` in den 30-Tage-Papierkorb.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "note": {"type": "string"},
            },
        },
    },
    {
        "name": "wiki_memory_undo",
        "description": "Macht eine eigene Revision in `memory/` anhand der `revision_id` rückgängig.",
        "inputSchema": {
            "type": "object",
            "required": ["revision_id"],
            "properties": {"revision_id": {"type": "integer"}},
        },
    },
]


async def _touch_named_token(request: Request) -> None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        raw = h[7:].strip()
        if raw.startswith("wk_"):
            th = hash_token(raw)
            async with session_scope(write=True) as db:
                await db.execute(update(McpToken).where(McpToken.token_hash == th).values(last_used_at=utcnow()))


async def _dispatch_tool(c: Caller, name: str, args: dict) -> dict:
    if name == "wiki_memory_index":
        perm.require_read(c, "memory")
        return {
            "index_markdown": read_vault_file("memory/_index"),
            "categories": list(perm.MEMORY_CATEGORIES),
        }

    if name == "wiki_search":
        sp = args.get("space")
        spaces = [sp] if sp else None
        if spaces:
            perm.require_read(c, sp)
        async with session_scope(write=False) as db:
            hits = await search_svc.search_pages(
                db,
                c,
                str(args.get("query") or ""),
                spaces=spaces,
                tag=args.get("tag"),
                folder=args.get("folder"),
                limit=min(int(args.get("limit") or 20), 60),
            )
            return {"results": hits}

    if name == "wiki_read":
        async with session_scope(write=False) as db:
            p = await store.get_page_or_404(db, str(args.get("path") or ""), c)
            detail = await store.page_detail_dict(db, p, c)
            detail.pop("raw", None)
            return detail

    if name == "wiki_list":
        sp = args.get("space")
        if sp:
            perm.require_read(c, sp)
        async with session_scope(write=False) as db:
            tree = await store.list_tree(db, c, space=sp)
            folder = (args.get("folder") or "").strip("/")
            if folder:
                tree["pages"] = [p for p in tree["pages"] if p["folder"] == folder or p["folder"].startswith(folder + "/")]
                tree["folders"] = [f for f in tree["folders"] if f == folder or f.startswith(folder + "/")]
            return tree

    if name == "wiki_backlinks":
        async with session_scope(write=False) as db:
            p = await store.get_page_or_404(db, str(args.get("path") or ""), c)
            d = await store.page_detail_dict(db, p, c)
            return {"path": p.path, "title": p.title, "backlinks": d["backlinks"], "outgoing_links": d["outgoing_links"]}

    if name == "wiki_recent_changes":
        sp = args.get("space")
        allowed = [s for s in ("privat", "memory", "ki") if perm.can_read_space(c, s) and (not sp or s == sp)]
        if sp and sp not in allowed:
            perm.require_read(c, sp)
        async with session_scope(write=False) as db:
            rows = (await db.scalars(
                select(Revision)
                .where(Revision.space.in_(allowed))
                .order_by(Revision.id.desc())
                .limit(min(int(args.get("limit") or 25), 100))
            )).all()
            return {"revisions": [store.revision_view(r) for r in rows]}

    if name == "wiki_memory_save":
        cat = str(args.get("category") or "").strip().lower()
        slug = str(args.get("slug") or "").strip().strip("/")
        if "/" in slug:
            raise Invalid("`slug` darf keinen Schrägstrich enthalten – wähle die passende `category`.")
        full_path = f"memory/{cat}/{slug}"
        fm: dict = {"summary": str(args.get("summary") or "")}
        for k in ("tags", "importance", "pinned", "expires_in_days"):
            if args.get(k) is not None:
                fm[k] = args[k]
        async with session_scope(write=True) as db:
            return await store.save_page(
                db,
                c,
                path=full_path,
                title=str(args.get("title") or ""),
                body=str(args.get("body") or ""),
                frontmatter=fm,
                note=str(args.get("note") or ""),
                force_new=bool(args.get("force_new", False)),
            )

    if name == "wiki_memory_patch":
        norm_p, space, _, _ = normalize_path(str(args.get("path") or ""))
        if space != "memory":
            raise Forbidden("Über MCP darf ausschließlich in `memory/` geschrieben werden (`privat/` und `ki/` sind schreibgeschützt).")
        fm_patch = {}
        if args.get("summary") is not None:
            fm_patch["summary"] = args["summary"]
        if args.get("tags") is not None:
            fm_patch["tags"] = args["tags"]
        async with session_scope(write=True) as db:
            return await store.patch_page(
                db,
                c,
                norm_p,
                old_text=args.get("old_text"),
                new_text=args.get("new_text"),
                heading=args.get("heading"),
                mode=str(args.get("mode") or "append"),
                frontmatter_patch=fm_patch or None,
                note=str(args.get("note") or ""),
            )

    if name == "wiki_memory_move":
        from_norm, s1, _, _ = normalize_path(str(args.get("from_path") or ""))
        to_norm, s2, _, _ = normalize_path(str(args.get("to_path") or ""))
        if s1 != "memory" or s2 != "memory":
            raise Forbidden("Über MCP können nur Seiten innerhalb von `memory/` verschoben werden.")
        async with session_scope(write=True) as db:
            return await store.move_page(db, c, from_norm, to_norm, new_title=args.get("title"))

    if name == "wiki_memory_delete":
        norm_p, space, _, _ = normalize_path(str(args.get("path") or ""))
        if space != "memory":
            raise Forbidden("Über MCP können ausschließlich Seiten in `memory/` gelöscht werden.")
        async with session_scope(write=True) as db:
            return await store.delete_page(db, c, norm_p, note=str(args.get("note") or ""))

    if name == "wiki_memory_undo":
        async with session_scope(write=True) as db:
            return await store.undo_revision(db, c, int(args["revision_id"]))

    raise Invalid(f"Unbekanntes MCP-Werkzeug: {name}")


async def _handle_rpc(c: Caller, msg: dict) -> dict | None:
    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}

    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                "serverInfo": {"name": "ida-wiki", "version": "1.0.0"},
                "instructions": MCP_INSTRUCTIONS,
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}

    if method == "tools/call":
        t_name = str(params.get("name") or "")
        t_args = params.get("arguments") or {}
        try:
            res = await _dispatch_tool(c, t_name, t_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
                    "isError": False,
                },
            }
        except (Forbidden, Invalid, NotFound, Conflict) as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Fehler: {e}"}],
                    "isError": True,
                },
            }

    if method == "resources/list":
        res_list = [
            {"uri": "wiki://memory/_index", "name": "KI-Gedächtnis – Index", "mimeType": "text/markdown"},
            {"uri": "wiki://memory/_schema", "name": "KI-Gedächtnis – Struktur-Schema", "mimeType": "text/markdown"},
        ]
        if c.allow_read_private:
            res_list.append({"uri": "wiki://privat/_start", "name": "Persönliches Wiki – Startseite", "mimeType": "text/markdown"})
        if c.allow_read_chats:
            res_list.append({"uri": "wiki://ki/_uebersicht", "name": "KI & Chats – Übersicht", "mimeType": "text/markdown"})
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": res_list}}

    if method == "resources/read":
        uri = str(params.get("uri") or "")
        ref = uri[7:] if uri.startswith("wiki://") else uri
        try:
            async with session_scope(write=False) as db:
                p = await store.get_page_or_404(db, ref, c)
                raw = read_vault_file(p.path)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"contents": [{"uri": f"wiki://{p.path}", "mimeType": "text/markdown", "text": raw}]},
            }
        except (Forbidden, Invalid, NotFound) as e:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": str(e)}}

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unbekannte Methode: {method}"}}


@router.post("/mcp")
async def mcp_post(request: Request, c: Caller = Depends(caller)):
    await _touch_named_token(request)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Ungültiges JSON"}}, status_code=400)

    if isinstance(payload, list):
        replies = []
        for item in payload:
            if isinstance(item, dict):
                r = await _handle_rpc(c, item)
                if r is not None:
                    replies.append(r)
        return Response(status_code=202) if not replies else JSONResponse(replies)

    if not isinstance(payload, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Ungültige Anfrage"}}, status_code=400)

    reply = await _handle_rpc(c, payload)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@router.get("/mcp")
async def mcp_get(_: Caller = Depends(caller)):
    # Streamable HTTP (2025-03-26): Server-zu-Client-SSE ist optional; 405 signalisiert reines Request/Response über POST /mcp.
    return Response(status_code=405, headers={"Allow": "POST"})
