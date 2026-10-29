"""Sets up the wiki once: the fixed structure for your life (Leben › areas, a template per area) and IDA's own
wiki user for the chats. Run with an admin login of the wiki:

    docker compose exec app python -m app.cli wiki-setup <admin-user> <admin-password> [<your-wiki-user>]

Pages that already exist are left alone, so it can run again any time (e.g. after a new area was added here).
"""
from xml.sax.saxutils import escape

import httpx

from ..config import get_settings
from .wiki import page_path, put_page

LIFE = "Simons Leben"  # title of the top page of the structure
# Only you and IDA may see these (rights per space; everybody else is locked out) …
PRIVATE_SPACES = [("Leben",), ("Ida",), ("Vorlagen",)]
# … the templates are not shown in the navigation (they are only reachable through "＋ Neuer Eintrag") …
HIDDEN_SPACES = [("Vorlagen",)]
# … and these apps do not show up in the "Applications" panel: (space, page that holds the app's UI extension)
HIDDEN_APPS = [(("Help",), "WebHome"), (("Sandbox",), "ApplicationsPanelEntry")]

# (space, title, what belongs here, template name, fields of the template, sections of the template)
AREAS = [
    ("Ich", "Ich", "Steckbrief, Ausweise und Dokumente, wichtige Nummern, Vorlieben – alles über dich selbst.",
     "Eintrag", ["Thema", "Datum"], ["Inhalt", "Dateien & Links"]),
    ("Menschen", "Menschen", "Familie, Freunde, Bekannte, Kontakte – eine Seite pro Mensch.",
     "Person", ["Name", "Beziehung", "Geburtstag", "Telefon", "E-Mail", "Adresse"], ["Wie wir uns kennen", "Notizen", "Geschenkideen", "Gemeinsame Erlebnisse"]),
    ("Gesundheit", "Gesundheit", "Ärzte, Medikamente, Befunde, Termine, Werte.",
     "Gesundheit", ["Art (Arzt / Medikament / Befund / Termin)", "Datum", "Arzt / Praxis"], ["Details", "Nächste Schritte"]),
    ("SchuleArbeit", "Schule & Arbeit", "Fächer, Kurse, Prüfungen, Jobs, Bewerbungen, Zeugnisse.",
     "Thema", ["Fach / Firma", "Zeitraum", "Status"], ["Inhalt", "Aufgaben", "Ergebnisse"]),
    ("Finanzen", "Finanzen", "Konten, Verträge, Abos, Versicherungen, größere Ausgaben.",
     "Vertrag", ["Anbieter", "Betrag", "Abrechnung (monatlich / jährlich)", "Läuft seit", "Kündigungsfrist", "Konto"], ["Details", "Unterlagen"]),
    ("Zuhause", "Zuhause", "Wohnung, Möbel, Geräte, Technik, Garantien, Anleitungen.",
     "Ding", ["Was", "Wo gekauft", "Gekauft am", "Preis", "Garantie bis"], ["Notizen", "Anleitung & Links"]),
    ("Projekte", "Projekte", "Alles, woran du baust oder arbeitest.",
     "Projekt", ["Ziel", "Status", "Gestartet", "Links"], ["Beschreibung", "Nächste Schritte", "Verlauf"]),
    ("Wissen", "Wissen", "Notizen, Anleitungen, Rezepte, Dinge, die du dir merken willst.",
     "Notiz", ["Thema", "Quelle"], ["Inhalt"]),
    ("OrteReisen", "Orte & Reisen", "Orte, an denen du warst oder hinwillst, Reisen, Ausflüge.",
     "Ort", ["Wo", "Wann", "Mit wem"], ["Erlebnisse", "Tipps", "Fotos & Links"]),
    ("ZieleIdeen", "Ziele & Ideen", "Was du erreichen willst, Ideen, Wünsche, Pläne.",
     "Ziel", ["Was", "Bis wann", "Status"], ["Warum", "Schritte", "Notizen"]),
    ("Tagebuch", "Tagebuch", "Ein Eintrag pro Tag oder Erlebnis.",
     "Tagebucheintrag", ["Datum", "Stimmung"], ["Was war heute", "Was war gut", "Gedanken"]),
]


def _tree(ref: str) -> str:
    return f'{{{{documentTree root="document:{ref}" showRoot="false" showTranslations="false" showAttachments="false"/}}}}'


def _new_button(space: str, template: str) -> str:
    return f"[[**＋ Neuer Eintrag ({template})**>>path:/bin/create/Leben/{space}/WebHome?templateprovider=Vorlagen.{template}]]"


def pages() -> list[tuple[tuple[str, ...], str, str, str]]:
    """(spaces, page name, title, content in xwiki/2.1) – everything this setup creates."""
    out = []
    rows = "\n".join(f"|[[{title}>>Leben.{space}.WebHome]]|{what}" for space, title, what, *_ in AREAS)
    out.append((("Leben",), "WebHome", LIFE, f"""= {LIFE} =

Alles an einem Ort, immer gleich aufgebaut. Jeder Bereich hat eine feste Vorlage: Neues legst du im passenden Bereich mit **„＋ Neuer Eintrag“** an – dann sieht jede Seite gleich aus und landet am richtigen Platz.

|=Bereich|=Was hier hinkommt
{rows}
|[[Ida-Chats>>Ida.Chats.WebHome]]|Alle Chats mit Ida – IDA schreibt sie automatisch hier hinein

== Alles im Überblick ==

{_tree("Leben.WebHome")}
"""))
    for space, title, what, template, fields, sections in AREAS:
        out.append((("Leben", space), "WebHome", title, f"""{what}

{_new_button(space, template)}

{_tree(f"Leben.{space}.WebHome")}
"""))
        table = "\n".join(f"|={f}|" for f in fields)
        body = "\n\n".join(f"== {s} ==\n\n" for s in sections)
        out.append((("Vorlagen",), f"{template}Template", f"Vorlage: {template}", f"{table}\n\n{body}"))
    out.append((("Vorlagen",), "WebHome", "Vorlagen",
                f"Die Vorlagen für die Bereiche unter [[{LIFE}>>Leben.WebHome]]. Ändern geht – dann sehen neue Einträge ab da so aus.\n\n"
                + _tree("Vorlagen.WebHome")))
    out.append((("Ida",), "WebHome", "Ida", "Was IDA ins Wiki schreibt.\n\n* [[Chats>>Ida.Chats.WebHome]]\n"))
    out.append((("Ida", "Chats"), "WebHome", "Chats mit Ida",
                "Jeder Chat aus IDA als eigene Seite. IDA schreibt sie nach jeder Antwort neu – bitte hier nichts von Hand ändern, "
                "das wird beim nächsten Mal überschrieben. In IDA gelöschte Chats bleiben hier als Archiv.\n\n" + _tree("Ida.Chats.WebHome")))
    return out


def _object(cls: str, props: dict[str, str]) -> bytes:
    p = "".join(f'<property name="{k}"><value>{escape(v)}</value></property>' for k, v in props.items())
    return f'<object xmlns="http://www.xwiki.org"><className>{cls}</className>{p}</object>'.encode("utf8")


async def _exists(c: httpx.AsyncClient, spaces, name) -> bool:
    r = await c.get(page_path(spaces, name), headers={"Accept": "application/xml"})
    return r.status_code == 200


async def _has_object(c: httpx.AsyncClient, spaces, name, cls) -> bool:
    r = await c.get(page_path(spaces, name) + f"/objects/{cls}", headers={"Accept": "application/xml"})
    return r.status_code == 200 and "<objectSummary" in r.text


async def _add_object(c: httpx.AsyncClient, spaces, name, cls, props) -> None:
    r = await c.post(page_path(spaces, name) + "/objects", content=_object(cls, props), headers={"Content-Type": "application/xml"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"{'.'.join(spaces)}.{name}: Objekt {cls} nicht angelegt ({r.status_code}: {r.text[:200]})")


async def _page_json(c: httpx.AsyncClient, spaces, name) -> dict | None:
    r = await c.get(page_path(spaces, name), headers={"Accept": "application/json"})
    return r.json() if r.status_code == 200 else None


async def _update_page(c, spaces, name, *, title=None, replace=(), hidden=None) -> bool:
    """Changes title / text / hidden of an existing page; True if it was changed."""
    d = await _page_json(c, spaces, name)
    if not d:
        return False
    new_title, content = title or d.get("title") or name, d.get("content") or ""
    for a, b in replace:
        content = content.replace(a, b)
    want_hidden = bool(d.get("hidden")) if hidden is None else hidden
    if (new_title, content, want_hidden) == (d.get("title"), d.get("content") or "", bool(d.get("hidden"))):
        return False
    await put_page(c, spaces, name, new_title, content, d.get("syntax") or "xwiki/2.1", hidden=want_hidden)
    return True


async def _restrict(c, spaces, users: list[str]) -> bool:
    """Only these users may see/edit the space (an allow rule locks everybody else out of that level)."""
    changed = False
    if not await _exists(c, spaces, "WebPreferences"):
        await put_page(c, spaces, "WebPreferences", "Rechte", "", "xwiki/2.1")
        changed = True
    if not await _has_object(c, spaces, "WebPreferences", "XWiki.XWikiRights"):
        await _add_object(c, spaces, "WebPreferences", "XWiki.XWikiRights",
                          {"users": ",".join(f"XWiki.{u}" for u in users), "groups": "", "levels": "view,edit,comment,delete", "allow": "1"})
        changed = True
    return changed


async def _hide_apps(c) -> list[str]:
    """Puts apps on the blacklist of the Applications panel; returns the ids that were added."""
    cfg, cls = (("PanelsCode",), "ApplicationsPanelConfiguration"), "PanelsCode.ApplicationsPanelBlackListClass"
    base = page_path(*cfg) + f"/objects/{cls}"
    j = {"Accept": "application/json"}
    have = set()
    for o in (await c.get(base, headers=j)).json().get("objectSummaries", []):
        d = (await c.get(f"{base}/{o['number']}", headers=j)).json()
        have |= {p["value"] for p in d["properties"] if p["name"] == "applicationId"}
    added = []
    for spaces, name in HIDDEN_APPS:
        r = await c.get(page_path(spaces, name) + "/objects/XWiki.UIExtensionClass/0", headers=j)
        if r.status_code != 200:
            continue
        app = next((p["value"] for p in r.json()["properties"] if p["name"] == "name"), "")
        if app and app not in have:
            await _add_object(c, *cfg, cls, {"applicationId": app})
            added.append(app)
    return added


async def setup(admin_user: str, admin_password: str, owner: str | None = None) -> list[str]:
    s = get_settings()
    if not s.wiki_url or not s.wiki_user or not s.wiki_password:
        raise RuntimeError("WIKI_URL, WIKI_USER und WIKI_PASSWORD in der .env setzen")
    done = []
    async with httpx.AsyncClient(base_url=s.wiki_url.rstrip("/"), auth=(admin_user, admin_password), timeout=60) as c:
        r = await c.get("/rest/wikis/xwiki", headers={"Accept": "application/xml"})
        if r.status_code != 200:
            raise RuntimeError(f"Wiki nicht erreichbar oder Login falsch ({r.status_code})")
        # IDA's own user for the chats
        user = ("XWiki",)
        if not await _exists(c, user, s.wiki_user):
            await put_page(c, user, s.wiki_user, s.wiki_user, "", syntax="xwiki/2.1")  # the user sheet shows it
        if not await _has_object(c, user, s.wiki_user, "XWiki.XWikiUsers"):
            await _add_object(c, user, s.wiki_user, "XWiki.XWikiUsers",
                              {"first_name": s.wiki_user, "last_name": "(IDA)", "active": "1", "password": s.wiki_password})
            done.append(f"Benutzer XWiki.{s.wiki_user} angelegt")
        if await _exists(c, user, "XWikiAllGroup"):
            members = await c.get(page_path(user, "XWikiAllGroup") + "/objects/XWiki.XWikiGroups", headers={"Accept": "application/xml"})
            if f"XWiki.{s.wiki_user}<" not in members.text:
                await _add_object(c, user, "XWikiAllGroup", "XWiki.XWikiGroups", {"member": f"XWiki.{s.wiki_user}"})
        # structure + templates (existing pages stay as they are)
        for spaces, name, title, content in pages():
            if not await _exists(c, spaces, name):
                await put_page(c, spaces, name, title, content, syntax="xwiki/2.1")
                done.append(f"{'.'.join(spaces)}.{name}")
        # a template provider per area: offers only its template, only in its area, and opens the new page in the editor
        for space, title, _what, template, *_ in AREAS:
            if not await _exists(c, ("Vorlagen",), template):
                await put_page(c, ("Vorlagen",), template, f"{template} ({title})", "", syntax="xwiki/2.1")
            if not await _has_object(c, ("Vorlagen",), template, "XWiki.TemplateProviderClass"):
                await _add_object(c, ("Vorlagen",), template, "XWiki.TemplateProviderClass", {
                    "name": f"{template} ({title})", "description": f"Neuer Eintrag im Bereich {title}",
                    "template": f"Vorlagen.{template}Template", "creationRestrictions": f"Leben.{space}",
                    "visibilityRestrictions": f"Leben.{space}", "creationRestrictionsAreSuggestions": "0",
                    "terminal": "0", "action": "edit"})
                done.append(f"Vorlage {template} für {title}")
        # polish for existing wikis (all of it can run again): the new name, rights, hidden templates, app list
        if await _update_page(c, ("Leben",), "WebHome", title=LIFE, replace=[("Mein Leben", LIFE)]):
            done.append(f"„{LIFE}“ umbenannt")
        if await _update_page(c, ("Vorlagen",), "WebHome", replace=[("Mein Leben", LIFE)]):
            done.append("Vorlagen-Startseite angepasst")
        for spaces in HIDDEN_SPACES:
            r = await c.get(page_path(spaces, "WebHome").rsplit("/pages/", 1)[0] + "/pages?hidden=true", headers={"Accept": "application/json"})
            for pg in r.json().get("pageSummaries", []):
                if await _update_page(c, spaces, pg["name"], hidden=True):
                    done.append(f"{'.'.join(spaces)}.{pg['name']} ausgeblendet")
        for app in await _hide_apps(c):
            done.append(f"App {app} aus dem Menü genommen")
        if owner:
            for spaces in PRIVATE_SPACES:
                if await _restrict(c, spaces, [owner, s.wiki_user]):
                    done.append(f"{'.'.join(spaces)} nur für {owner} und {s.wiki_user}")
        else:
            done.append("Zugriff nicht eingeschränkt (Benutzernamen als dritten Wert angeben)")
    return done
