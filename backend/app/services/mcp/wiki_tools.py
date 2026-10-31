"""wiki_*-Tools des internen MCP: Ida bedient den lokalen Wiki-Dienst (siehe docs/WIKI.md).

* memory/: direkt lesen & strukturiert schreiben
* privat/: nur lesen; Schreiben erzeugt ein Freigabe-Proposal und wartet im Live-Chat auf den Nutzer
* ki/: nur lesen (Spiegel kommt aus IDA, nicht aus den Tools)
"""
from ...config import get_settings
from .. import wiki_client as wc
from ..code import approvals
from ..errors import Invalid

ALL = "all"
E = {"type": "string"}

_MEM_CATS = ["kern", "personen", "projekte", "konzepte-wissen", "entscheidungen", "agenten", "journal"]


def enabled() -> bool:
    return wc.enabled()


def spec() -> list[tuple[str, str, str, dict]]:
    obj = lambda props, req=(): {"type": "object", "properties": props, **({"required": list(req)} if req else {})}  # noqa: E731
    path = {**E, "description": "Seitenpfad, z. B. memory/projekte/ida oder privat/00-inbox/notiz (ohne .md)"}
    space = {"type": "string", "enum": ["memory", "privat", "ki"], "description": "Bereich eingrenzen"}
    cat = {"type": "string", "enum": _MEM_CATS, "description": "Kategorie unter memory/"}
    return [
        (ALL, "wiki_search", "Volltextsuche im Wiki (memory, privat, ki). Wortanfänge reichen; -wort schließt aus.",
         obj({"query": E, "space": space, "tag": E, "folder": E, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ("query",))),
        (ALL, "wiki_read", "Liest eine Wiki-Seite (Inhalt, Frontmatter, Links, Backlinks, Anhänge).", obj({"path": path}, ("path",))),
        (ALL, "wiki_list", "Listet Ordner und Seiten eines Bereichs.", obj({"space": space, "folder": E})),
        (ALL, "wiki_backlinks", "Eingehende und ausgehende [[Wikilinks]] einer Seite.", obj({"path": path}, ("path",))),
        (ALL, "wiki_memory_index", "Liest den Katalog memory/_index (Übersicht aller Gedächtnis-Seiten).", obj({})),
        (ALL, "wiki_memory_save",
         "Erstellt oder aktualisiert eine strukturierte Seite in memory/<kategorie>/<slug>. "
         "Bevorzuge bestehende Seiten (Upsert); force_new nur wenn wirklich neu. Erlaubte Kategorien: "
         + ", ".join(_MEM_CATS) + ".",
         obj({"category": cat, "slug": E, "title": E, "body": E, "summary": E,
              "tags": {"type": "array", "items": E}, "importance": {"type": "integer", "minimum": 1, "maximum": 5},
              "pinned": {"type": "boolean"}, "force_new": {"type": "boolean"}}, ("category", "slug", "body"))),
        (ALL, "wiki_memory_patch",
         "Ändert gezielt einen Abschnitt oder Text in einer memory/-Seite (statt alles zu überschreiben).",
         obj({"path": path, "heading": E, "old_text": E, "new_text": E,
              "mode": {"type": "string", "enum": ["append", "replace", "prepend"], "description": "Standard append unter heading"},
              "title": E, "summary": E}, ("path",))),
        (ALL, "wiki_private_write",
         "Schlägt eine Änderung im privaten Wiki (privat/) vor. Im Live-Chat erscheint eine Freigabe-Karte mit Diff; "
         "erst nach Erlaubnis des Nutzers wird geschrieben. action: create|patch|update|move|delete.",
         obj({"action": {"type": "string", "enum": ["create", "patch", "update", "move", "delete"]},
              "path": path, "to_path": E, "title": E, "body": E, "heading": E, "old_text": E, "new_text": E,
              "mode": {"type": "string", "enum": ["append", "replace", "prepend"]},
              "summary": {**E, "description": "Kurz warum / was geändert wird (für die Freigabe-Karte)"}}, ("action", "path"))),
    ]


async def call(db, me, name: str, a: dict) -> dict:
    by = me.agent_id or "main"
    try:
        if name == "wiki_search":
            return await wc.request("GET", "/api/v1/search", by=by, params={
                k: v for k, v in {
                    "q": a.get("query") or "", "space": a.get("space"), "tag": a.get("tag"),
                    "folder": a.get("folder"), "limit": a.get("limit") or 20,
                }.items() if v is not None and v != ""
            })
        if name == "wiki_read":
            return await wc.request("GET", "/api/v1/page", by=by, params={"path": a.get("path")})
        if name == "wiki_list":
            return await wc.request("GET", "/api/v1/tree", by=by, params={
                k: v for k, v in {"space": a.get("space"), "folder": a.get("folder")}.items() if v
            })
        if name == "wiki_backlinks":
            page = await wc.request("GET", "/api/v1/page", by=by, params={"path": a.get("path")})
            return {"path": page.get("path") or a.get("path"), "links": page.get("links") or [],
                    "backlinks": page.get("backlinks") or []}
        if name == "wiki_memory_index":
            return await wc.request("GET", "/api/v1/page", by=by, params={"path": "memory/_index"})
        if name == "wiki_memory_save":
            cat = str(a.get("category") or "").strip()
            slug = str(a.get("slug") or "").strip().strip("/")
            if cat not in _MEM_CATS:
                raise Invalid(f"Ungültige Kategorie. Erlaubt: {', '.join(_MEM_CATS)}")
            if not slug:
                raise Invalid("slug fehlt")
            path = f"memory/{cat}/{slug}"
            body = {
                "path": path,
                "title": a.get("title") or slug.replace("-", " "),
                "body": a.get("body") or "",
                "frontmatter": {
                    "summary": a.get("summary") or "",
                    "category": cat,
                    "tags": a.get("tags") or [],
                    "importance": a.get("importance") or 3,
                    "pinned": bool(a.get("pinned")),
                },
                "force_new": bool(a.get("force_new")),
                "note": f"von Agent {by}",
            }
            out = await wc.request("POST", "/api/v1/pages", by=by, json=body)
            wc.invalidate()
            return _page_ok(out, "Gespeichert")
        if name == "wiki_memory_patch":
            path = str(a.get("path") or "")
            if not path.startswith("memory/"):
                raise Invalid("wiki_memory_patch nur für Seiten unter memory/")
            patch = {
                "ref": path,
                "heading": a.get("heading"),
                "old_text": a.get("old_text"),
                "new_text": a.get("new_text"),
                "mode": a.get("mode") or "append",
                "title": a.get("title"),
                "note": f"von Agent {by}",
            }
            if a.get("summary") is not None:
                patch["frontmatter_patch"] = {"summary": a.get("summary")}
            out = await wc.request("POST", "/api/v1/pages/patch", by=by, json=patch)
            wc.invalidate()
            return _page_ok(out, "Aktualisiert")
        if name == "wiki_private_write":
            return await _private_write(me, a)
    except wc.WikiOffline as e:
        return {"error": str(e), "hinweis": "Das Wiki ist gerade nicht erreichbar – später erneut versuchen."}
    except Invalid as e:
        return {"error": str(e)}
    return {"error": f"Unbekanntes Wiki-Tool: {name}"}


def _page_ok(out: dict, verb: str) -> dict:
    page = out.get("page") or out
    path = page.get("path") or ""
    url = f"{wc.public_url()}/?seite={path}" if wc.public_url() and path else ""
    return {"ok": True, "aktion": verb, "path": path, "title": page.get("title"),
            "revision": page.get("revision"), **({"url": url} if url else {}),
            **({k: out[k] for k in ("hint", "duplicate_of", "detail") if k in out})}


async def _private_write(me, a: dict) -> dict:
    action = str(a.get("action") or "").strip()
    path = str(a.get("path") or "").strip()
    if not path.startswith("privat/"):
        raise Invalid("wiki_private_write nur für Pfade unter privat/")
    payload = {k: a[k] for k in (
        "path", "to_path", "title", "body", "heading", "old_text", "new_text", "mode", "summary", "tags"
    ) if a.get(k) is not None}
    prop = await wc.request("POST", "/api/v1/internal/proposals", by="user", json={
        "agent_id": me.agent_id or "main", "action": action, "payload": payload,
    })
    view = prop.get("proposal") or prop
    proposal_id = view.get("id") or prop.get("proposal_id")
    secret = prop.get("commit_secret") or ""
    diff = view.get("diff_preview") or prop.get("diff") or ""
    summary = view.get("summary") or a.get("summary") or f"{action} {path}"

    # Live-Chat: Freigabe-Karte (wie Code-Nachfragen). Hintergrund: Proposal bleibt im Wiki offen.
    if me.interactive and me.chat_id:
        decision = await approvals.ask(me.chat_id, "wiki_private_write", {
            "path": path,
            "action": action,
            "summary": summary,
            "diff_preview": diff[:6000],
            "proposal_id": proposal_id,
        })
        if decision.get("behavior") != "allow":
            try:
                await wc.request("POST", f"/api/v1/internal/proposals/{proposal_id}/reject", by="user", json={})
            except Exception:
                pass
            return {"ok": False, "abgelehnt": True, "proposal_id": proposal_id,
                    "hinweis": decision.get("message") or "Der Nutzer hat die Änderung abgelehnt."}
        out = await wc.request("POST", f"/api/v1/internal/proposals/{proposal_id}/commit", by="user",
                               json={"commit_secret": secret})
        wc.invalidate()
        return {"ok": True, "freigegeben": True, "proposal_id": proposal_id, **_page_ok(out, "Privat geschrieben")}

    wiki = wc.public_url() or "Wiki"
    return {
        "ok": True, "wartet_auf_freigabe": True, "proposal_id": proposal_id,
        "summary": summary, "diff_preview": diff[:2000],
        "hinweis": f"Kein Live-Chat – der Vorschlag liegt im Wiki zur Freigabe bereit ({wiki}). "
                   "Sage dem Nutzer, er soll ihn dort annehmen oder ablehnen.",
    }
