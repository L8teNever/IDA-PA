"""wiki_*-Tools des internen MCP: Ida bedient den lokalen Wiki-Dienst (siehe docs/WIKI.md).

* memory/: direkt lesen & strukturiert schreiben
* privat/: direkt lesen, anlegen, ändern, verschieben und Ordner anlegen (wie memory/, aber freie Ordner des Nutzers);
  nur Löschen erzeugt ein Freigabe-Proposal und wartet im Live-Chat auf den Nutzer
* ki/: nur lesen (Spiegel kommt aus IDA, nicht aus den Tools)
"""
from ...config import get_settings
from .. import wiki_client as wc
from ..code import approvals
from ..errors import Invalid, NotFound

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
        (ALL, "wiki_private_save",
         "Legt eine Seite im privaten Wiki (privat/) an oder überschreibt sie – direkt, ohne Freigabe. privat/ sind die Ordner des "
         "Nutzers für alles, was er selbst schnell nachschlagen will (z. B. privat/04-arbeit-projekte/projektarbeit/...). "
         "Nimm einen vorhandenen Ordner, wenn einer passt (siehe Ordnerübersicht im Kontext oder wiki_list space=privat); "
         "neue Ordner entstehen automatisch mit dem Pfad. Bestehende Seiten lieber mit wiki_private_patch ergänzen.",
         obj({"path": path, "title": E, "body": E, "tags": {"type": "array", "items": E}, "summary": E}, ("path", "body"))),
        (ALL, "wiki_private_patch",
         "Ändert gezielt einen Abschnitt oder Text einer Seite in privat/ (direkt, ohne Freigabe).",
         obj({"path": path, "heading": E, "old_text": E, "new_text": E,
              "mode": {"type": "string", "enum": ["append", "replace", "prepend"], "description": "Standard append unter heading"},
              "title": E}, ("path",))),
        (ALL, "wiki_private_move",
         "Verschiebt oder benennt eine Seite in privat/ um (direkt); [[Links]] auf die Seite werden automatisch angepasst.",
         obj({"path": path, "to_path": {**E, "description": "Neuer Pfad unter privat/"}, "title": E}, ("path", "to_path"))),
        (ALL, "wiki_private_folder",
         "Legt einen (auch leeren) Ordner in privat/ an, z. B. privat/04-arbeit-projekte/projektarbeit.",
         obj({"folder": E}, ("folder",))),
        (ALL, "wiki_private_write",
         "Löschen im privaten Wiki (privat/): Im Live-Chat erscheint eine Freigabe-Karte, erst nach Erlaubnis des Nutzers wird gelöscht "
         "(die Seite landet im Papierkorb). Zum Anlegen/Ändern/Verschieben NICHT nötig – dafür wiki_private_save/_patch/_move. "
         "Andere Aktionen (create|patch|update|move) gehen hier nur, wenn der Nutzer ausdrücklich vorher sehen will, was sich ändert.",
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
            page = await wc.request("GET", "/api/v1/page", by=by, params={"ref": a.get("path")})
            page.pop("raw", None)  # doppelt zu body + frontmatter
            return page
        if name == "wiki_list":
            tree = await wc.request("GET", "/api/v1/tree", by=by, params={
                k: v for k, v in {"space": a.get("space")}.items() if v
            })
            folder = str(a.get("folder") or "").strip().strip("/")
            if folder:  # der Wiki-Dienst filtert /tree nicht nach Ordner
                inside = lambda f: f == folder or f.startswith(folder + "/")  # noqa: E731
                tree["folders"] = [f for f in tree.get("folders") or [] if inside(f)]
                tree["pages"] = [p for p in tree.get("pages") or [] if inside(p.get("folder") or "")]
            return tree
        if name == "wiki_backlinks":
            page = await wc.request("GET", "/api/v1/page", by=by, params={"ref": a.get("path")})
            return {"path": page.get("path") or a.get("path"), "links": page.get("outgoing_links") or page.get("links") or [],
                    "backlinks": page.get("backlinks") or []}
        if name == "wiki_memory_index":
            return await wc.request("GET", "/api/v1/page", by=by, params={"ref": "memory/_index"})
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
        if name == "wiki_private_save":
            path = _private_path(a.get("path"))
            fm = {}
            try:  # bestehende Seite: ihr Frontmatter (Tags, eigene Felder des Nutzers) bleibt erhalten
                old = await wc.request("GET", "/api/v1/page", by=by, params={"ref": path})
                fm = dict(old.get("frontmatter") or {})
            except NotFound:
                pass
            fm.update({k: v for k, v in {"tags": a.get("tags"), "summary": a.get("summary")}.items() if v})
            body = {
                "path": path,
                "title": a.get("title") or None,
                "body": a.get("body") or "",
                "frontmatter": fm,
                "note": f"von Agent {by}",
            }
            out = await wc.request("POST", "/api/v1/pages", by=by, json=body)
            wc.invalidate()
            return _page_ok(out, "Privat gespeichert")
        if name == "wiki_private_patch":
            out = await wc.request("POST", "/api/v1/pages/patch", by=by, json={
                "ref": _private_path(a.get("path")),
                "heading": a.get("heading"), "old_text": a.get("old_text"), "new_text": a.get("new_text"),
                "mode": a.get("mode") or "append", "title": a.get("title"), "note": f"von Agent {by}",
            })
            wc.invalidate()
            return _page_ok(out, "Privat aktualisiert")
        if name == "wiki_private_move":
            out = await wc.request("POST", "/api/v1/pages/move", by=by, json={
                "ref": _private_path(a.get("path")), "to_path": _private_path(a.get("to_path")), "title": a.get("title"),
            })
            wc.invalidate()
            return {**_page_ok(out, "Verschoben"), "angepasste_links": out.get("rewritten_backlinks", 0)}
        if name == "wiki_private_folder":
            folder = str(a.get("folder") or "").strip().strip("/")
            if not folder.startswith("privat/"):
                raise Invalid("wiki_private_folder nur für Ordner unter privat/")
            out = await wc.request("POST", "/api/v1/folders", by=by, json={"folder": folder})
            wc.invalidate()
            return {"ok": True, "folder": out.get("folder") or folder}
        if name == "wiki_private_write":
            return await _private_write(me, a)
    except wc.WikiOffline as e:
        return {"error": str(e), "hinweis": "Das Wiki ist gerade nicht erreichbar – später erneut versuchen."}
    except (Invalid, NotFound) as e:
        return {"error": str(e)}
    return {"error": f"Unbekanntes Wiki-Tool: {name}"}


def _private_path(raw) -> str:
    path = str(raw or "").strip().strip("/")
    if path.lower().endswith(".md"):
        path = path[:-3]
    if not path.startswith("privat/") or path.count("/") < 1 or path == "privat/":
        raise Invalid("Nur Pfade unter privat/ (z. B. privat/00-inbox/notiz)")
    return path


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
