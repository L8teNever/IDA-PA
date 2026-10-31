"""Erstausstattung des IDA Wiki: Lebensordner in `privat/`, Struktur-Schema & Kern-Seiten in `memory/`, Übersicht in `ki/`."""
from sqlalchemy import select

from .auth import Caller
from .config import get_settings
from .db import session_scope
from .models import Page, Setting
from .services import chat_mirror, memory_guard, store
from .services.permissions import MEMORY_CATEGORIES, PRIVAT_DEFAULT_FOLDERS

PRIVAT_START_MD = """# Willkommen in deinem persönlichen Wiki

Dein privater Wissensspeicher ist wie ein lebenslanges zweites Gehirn aufgebaut: echte Markdown-Dateien, verknüpft über `[[Wikilinks]]`, mit Dateianhängen, interaktivem Wissensgraph und lückenlosem Versionsverlauf.

> [!tip] Drei klar getrennte Bereiche
> - **Privat (`privat/`)** – gehört ausschließlich dir. Externe MCP-Werkzeuge können hier nur lesen; IDA darf Änderungen nur mit deiner ausdrücklichen Freigabe vornehmen.
> - **KI-Gedächtnis (`memory/`)** – das gemeinsame, streng strukturierte Langzeitgedächtnis aller deiner KIs (siehe [[memory/_index|Gedächtnis-Index]]).
> - **KI & Chats (`ki/`)** – das automatische Live-Archiv deiner IDA-Agenten, Chats und Code-Projekte (siehe [[ki/_uebersicht|KI-Übersicht]]).

## Deine Lebensbereiche

- **00 Inbox (`privat/00-inbox/`)** – Schnelle Gedanken, Notizen und Web-Fundstücke zum späteren Einordnen
- **01 Alltag & Wohnen (`privat/01-alltag-wohnen/`)** – Haushalt, Wohnung, Verträge, Nachbarschaft, Geräte
- **02 Finanzen & Steuern (`privat/02-finanzen-steuern/`)** – Konten, Versicherungen, Verträge, Steuerunterlagen
- **03 Gesundheit & Sport (`privat/03-gesundheit-sport/`)** – Vorsorge, Training, Rezepte, Routinen
- **04 Arbeit & Projekte (`privat/04-arbeit-projekte/`)** – Berufliches, eigene Vorhaben, Technik & Ideen (Vorlage: [[privat/08-dokumente-vorlagen/_templates/projekt|Projekt-Vorlage]])
- **05 Wissen & Lernen (`privat/05-wissen-lernen/`)** – Bücher, Kurse, Zusammenfassungen, Nachschlagewissen
- **06 Menschen & Beziehungen (`privat/06-menschen-beziehungen/`)** – Familie, Freunde, Geschenkideen, wichtige Kontakte
- **07 Reisen & Freizeit (`privat/07-reisen-freizeit/`)** – Reisepläne, Packlisten, Ausflüge, Hobbys
- **08 Dokumente & Vorlagen (`privat/08-dokumente-vorlagen/`)** – Checklisten, Textbausteine und [[privat/08-dokumente-vorlagen/_templates/notiz|Notiz-Vorlage]]
- **99 Archiv (`privat/99-archiv/`)** – Abgeschlossene Projekte und historische Unterlagen
"""

TEMPLATE_PROJEKT_MD = """# Projektname

> [!info] Kurzbeschreibung
> Worum geht es in diesem Projekt und welches Ziel soll erreicht werden?

## Nächste Schritte

- [ ] Ersten Meilenstein festlegen
- [ ] Wichtige Unterlagen verlinken

## Notizen & Entscheidungen

- Verknüpfte Seiten: [[privat/_start|Startseite]]
"""

TEMPLATE_NOTIZ_MD = """# Titel der Notiz

## Kernpunkte

- Punkt 1
- Punkt 2

## Verweise

- [[privat/_start|Startseite]]
"""

MEMORY_SCHEMA_MD = """# Struktur-Schema für das KI-Gedächtnis (`memory/`)

Dieser Bereich ist das **gemeinsame Langzeitgedächtnis** aller IDA-Agenten sowie extern angebundener KI-Clients (Claude Code, Cursor, Desktop-KIs über `/mcp`).

> [!important] Goldene Regeln für alle KI-Agenten
> 1. **Erst suchen, dann schreiben:** Prüfe zuerst [[memory/_index|den zentralen Index]] oder nutze `wiki_search`, ob zum Thema bereits eine Seite existiert.
> 2. **Bestehende Dossiers pflegen statt Einzelnotizen anzulegen:** Neue Fakten zu einer Person gehören in `memory/personen/<name>`, Projekt-Details in `memory/projekte/<projekt>`. Nutze dafür bevorzugt `wiki_memory_patch`.
> 3. **Feste Kategorien einhalten:** Seiten dürfen ausschließlich in den 7 vorgesehenen Unterordnern liegen:
>    - `memory/kern/` – Grundfakten über den Nutzer, dauerhafte Arbeitsregeln & aktuelle Fokusse (`pinned: true` möglich)
>    - `memory/personen/` – Eine Dossier-Seite pro Person / Kontakt
>    - `memory/projekte/` – Laufende Projekte, Infrastruktur, Repositories & Systemlandschaft
>    - `memory/konzepte-wissen/` – Gelernte Lösungen, Workflows, Best Practices
>    - `memory/entscheidungen/` – Wichtige technische oder persönliche Entscheidungen inkl. Begründung
>    - `memory/agenten/` – Spezialwissen einzelner IDA-Subagenten (`memory/agenten/<agent_id>`)
>    - `memory/journal/` – Zeitgebundene Notizen (gerne mit Ablaufdatum `expires_in_days`)
> 4. **Querverweise mit `[[Wikilinks]]`:** Verlinke verwandte Seiten im Gedächtnis, in `privat/` oder relevante Gespräche in `ki/`.
> 5. **Keine Geheimnisse:** Niemals Passwörter, API-Keys, Tokens oder private Schlüssel speichern.
"""


async def seed() -> None:
    s = get_settings()
    # Physische Standard-Ordner immer sicherstellen (auch nach frischem Volume-Mount)
    for folder in PRIVAT_DEFAULT_FOLDERS:
        (s.vault_dir / folder).mkdir(parents=True, exist_ok=True)
    (s.vault_dir / "privat/08-dokumente-vorlagen/_templates").mkdir(parents=True, exist_ok=True)
    for cat in MEMORY_CATEGORIES:
        (s.vault_dir / "memory" / cat).mkdir(parents=True, exist_ok=True)
    (s.vault_dir / "ki").mkdir(parents=True, exist_ok=True)

    async with session_scope(write=True) as db:
        already = await db.get(Setting, "seeded_v1")
        sys_user = Caller("user", "user", via_override="sync")

        if not already:
            await store.save_page(
                db,
                sys_user,
                path="privat/_start",
                title="Startseite – Persönliches Wiki",
                body=PRIVAT_START_MD,
                frontmatter={"summary": "Startseite und Wegweiser durch deine persönlichen Lebensbereiche", "tags": ["start", "wegweiser"], "pinned": True},
                note="Erstausstattung",
            )
            await store.save_page(
                db,
                sys_user,
                path="privat/08-dokumente-vorlagen/_templates/projekt",
                title="Vorlage: Projekt",
                body=TEMPLATE_PROJEKT_MD,
                frontmatter={"summary": "Standardvorlage für neue Projekte mit Checkliste", "tags": ["vorlage", "projekt"]},
                note="Erstausstattung",
            )
            await store.save_page(
                db,
                sys_user,
                path="privat/08-dokumente-vorlagen/_templates/notiz",
                title="Vorlage: Notiz",
                body=TEMPLATE_NOTIZ_MD,
                frontmatter={"summary": "Kompakte Vorlage für Wissens- und Alltagsnotizen", "tags": ["vorlage"]},
                note="Erstausstattung",
            )
            await store.save_page(
                db,
                sys_user,
                path="memory/_schema",
                title="Struktur-Schema – KI-Gedächtnis",
                body=MEMORY_SCHEMA_MD,
                frontmatter={"summary": "Verbindliche Ordnungs- und Schreibregeln für alle KI-Agenten in memory/", "tags": ["schema", "gedaechtnis"]},
                note="Erstausstattung",
                managed_by="system",
            )
            await store.save_page(
                db,
                sys_user,
                path="memory/kern/ueber-mich",
                title="Über mich – Grundfakten",
                body="# Über mich\n\nHier stehen dauerhafte Grundfakten über dich, die alle deine KI-Assistenten kennen sollen.\n",
                frontmatter={"summary": "Dauerhafte Grundfakten über den Nutzer", "category": "kern", "importance": 5, "pinned": True, "tags": ["kern"]},
                note="Erstausstattung",
            )
            await store.save_page(
                db,
                sys_user,
                path="memory/kern/vorlieben-regeln",
                title="Vorlieben & Arbeitsregeln",
                body="# Vorlieben & Arbeitsregeln\n\n- Antworte präzise, klar strukturiert und auf Deutsch.\n- Pflege Wissen geordnet in bestehenden Dossier-Seiten weiter.\n",
                frontmatter={"summary": "Bevorzugter Kommunikationsstil und dauerhafte Regeln für KIs", "category": "kern", "importance": 5, "pinned": True, "tags": ["kern", "regeln"]},
                note="Erstausstattung",
            )
            await store.save_page(
                db,
                sys_user,
                path="memory/kern/aktuelle-fokusse",
                title="Aktuelle Fokusse",
                body="# Aktuelle Fokusse\n\nWoran du aktuell schwerpunktmäßig arbeitest.\n",
                frontmatter={"summary": "Aktuelle Schwerpunkte und laufende Prioritäten", "category": "kern", "importance": 4, "pinned": True, "tags": ["kern", "fokus"]},
                note="Erstausstattung",
            )
            await memory_guard.rebuild_memory_index(db)
            await chat_mirror.rebuild_ki_overview(db)
            db.add(Setting(key="seeded_v1", value={"ok": True}))
        else:
            # Sicherstellen, dass System-Übersichten existieren
            if not await db.scalar(select(Page).where(Page.path == "memory/_index")):
                await memory_guard.rebuild_memory_index(db)
            if not await db.scalar(select(Page).where(Page.path == "ki/_uebersicht")):
                await chat_mirror.rebuild_ki_overview(db)
