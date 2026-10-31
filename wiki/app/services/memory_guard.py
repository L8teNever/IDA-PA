"""Struktur-Wächter für `memory/` (den gemeinsamen Gedächtnis-Hauptordner aller KI-Agents).

Stellt sicher, dass KI-Schreibvorgänge niemals unstrukturierte lose Dateien, Geheimnisse oder Dubletten anlegen
und dass `memory/_index.md` nach jeder Änderung automatisch aktuell bleibt.
"""
import hashlib
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Caller
from ..config import get_settings
from ..db import utcnow
from ..errors import Invalid
from ..models import Page, PageLink
from . import links as link_svc
from .links import normalize_tag
from .permissions import MEMORY_CATEGORIES
from .vault import compose_markdown, slugify, write_vault_file

SECRET_RE = re.compile(
    r"\b(sk-[\w-]{16,}|ghp_\w{20,}|github_pat_\w{20,}|xox[bpa]-[\w-]{10,}|AIza[\w-]{20,}|AKIA\w{12,}|wk_[0-9a-f]{32,})"
    r"|bearer\s+[\w.~+/=-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY",
    re.I,
)

CATEGORY_LABELS = {
    "kern": ("Kern-Wissen & Regeln", "Immer relevante Grundfakten über den Nutzer, Vorlieben und aktuelle Fokusse"),
    "personen": ("Personen & Kontakte", "Eine Dossier-Seite pro Person statt verstreuter Einzelfakten"),
    "projekte": ("Projekte & Systeme", "Architektur, Tech-Stack, Infrastruktur und Status laufender Projekte"),
    "konzepte-wissen": ("Konzepte & Fachwissen", "Gelernte Lösungen, Workflows, Anleitungen und Referenzwissen"),
    "entscheidungen": ("Entscheidungen", "Wichtige Architekturentscheidungen und ihre Begründung"),
    "agenten": ("Agenten-Notizen", "Spezialwissen einzelner IDA-Subagenten"),
    "journal": ("Journal & Zeitgebundenes", "Chronologische Ereignisse und temporäre Notizen"),
}

SYSTEM_MEMORY_PAGES = {"memory/_index", "memory/_schema"}


def check_no_secrets(text: str) -> None:
    if SECRET_RE.search(text or ""):
        raise Invalid("Das sieht nach einem Passwort, API-Schlüssel oder Token aus – Geheimnisse dürfen nicht im Wiki-Gedächtnis gespeichert werden.")


def validate_memory_write(
    caller: Caller,
    path: str,
    title: str,
    body: str,
    frontmatter: dict | None,
    *,
    creating: bool,
) -> dict:
    """Prüft Pfad, Kategorie, Geheimnisse und Frontmatter für Seiten in `memory/`.
    Für KI-Aufrufer (`ida` und `mcp`) gelten strenge Schema-Regeln; der Nutzer selbst darf in der UI auch `_schema.md` bearbeiten.
    """
    is_ai = caller.kind in ("ida", "mcp")
    if is_ai:
        check_no_secrets(title)
        check_no_secrets(body)
        if path in SYSTEM_MEMORY_PAGES:
            raise Invalid(f"`{path}` wird vom Wiki-System automatisch verwaltet und kann nicht direkt von KIs überschrieben werden.")

    parts = path.split("/")
    if len(parts) < 3 and path not in SYSTEM_MEMORY_PAGES:
        raise Invalid(
            f"Seiten in `memory/` müssen in einer der festen Kategorien liegen: "
            f"{', '.join('memory/' + c + '/<seite>' for c in MEMORY_CATEGORIES)}"
        )

    cat = parts[1].lower() if len(parts) >= 3 else "system"
    if len(parts) >= 3 and cat not in MEMORY_CATEGORIES:
        raise Invalid(
            f"Unbekannte Memory-Kategorie „{cat}“. Erlaubte Ordner unter `memory/` sind ausschließlich: "
            f"{', '.join(MEMORY_CATEGORIES)}"
        )

    fm = dict(frontmatter or {})
    summary = str(fm.get("summary") or "").strip()
    if is_ai and creating and not summary:
        first_line = next(
            (ln.strip(" #>*-\t") for ln in (body or "").splitlines() if ln.strip() and not ln.strip().startswith("#")),
            "",
        )
        if not first_line:
            raise Invalid("Bitte `summary` (kurze 1-Satz-Zusammenfassung für `memory/_index.md`) oder aussagekräftigen Inhalt angeben.")
        summary = first_line[:160]
    if summary:
        check_no_secrets(summary)
        fm["summary"] = summary[:240]

    if cat in MEMORY_CATEGORIES:
        fm["category"] = cat

    raw_tags = fm.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [x.strip() for x in raw_tags.split(",") if x.strip()]
    if isinstance(raw_tags, list):
        clean_tags = []
        for t in raw_tags[:10]:
            nt = normalize_tag(str(t))
            if nt and nt not in clean_tags:
                clean_tags.append(nt)
        if clean_tags:
            fm["tags"] = clean_tags

    imp = fm.get("importance")
    if imp is not None:
        try:
            imp_i = int(imp)
        except (TypeError, ValueError) as e:
            raise Invalid("importance muss eine ganze Zahl zwischen 1 und 5 sein") from e
        if not 1 <= imp_i <= 5:
            raise Invalid("importance muss zwischen 1 und 5 liegen")
        fm["importance"] = imp_i
    else:
        fm["importance"] = 5 if cat == "kern" else 3

    if fm.get("pinned"):
        if cat != "kern" and is_ai:
            raise Invalid("Nur Seiten in `memory/kern/` dürfen von KIs als `pinned=true` (immer im Prompt) markiert werden.")
        fm["pinned"] = bool(fm["pinned"])

    if "expires_in_days" in fm:
        days = fm.pop("expires_in_days")
        if days:
            d = int(days)
            if not 1 <= d <= 3650:
                raise Invalid("expires_in_days muss zwischen 1 und 3650 liegen")
            fm["expires_at"] = (utcnow() + timedelta(days=d)).isoformat()

    fm["title"] = title
    fm["updated_by"] = caller.by
    fm["updated_at"] = datetime.now(ZoneInfo(get_settings().timezone)).isoformat(timespec="seconds")
    return fm


def _token_set(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9äöüß]{3,}", (text or "").lower())}


async def check_near_duplicate(db: AsyncSession, path: str, title: str, *, force_new: bool = False) -> None:
    """Verhindert, dass KI-Agents mehrere fast identische Seiten in `memory/` anlegen."""
    if force_new:
        return
    want_slug = slugify(title, "")
    want_tokens = _token_set(title)
    rows = (await db.scalars(select(Page).where(Page.space == "memory", Page.deleted_at.is_(None)))).all()
    for p in rows:
        if p.path in SYSTEM_MEMORY_PAGES or p.path == path:
            continue
        if want_slug and p.slug == want_slug:
            raise Invalid(
                f"Im KI-Gedächtnis existiert bereits eine passende Seite unter `{p.path}` („{p.title}“). "
                f"Bitte aktualisiere diese bestehende Seite mit `wiki_memory_patch` statt eine Dublette anzulegen "
                f"(oder setze `force_new=true`, falls es wirklich ein eigenes Thema ist)."
            )
        if len(want_tokens) >= 2:
            other = _token_set(p.title)
            if other:
                jacc = len(want_tokens & other) / len(want_tokens | other)
                if jacc >= 0.8:
                    raise Invalid(
                        f"Sehr ähnliche Seite bereits vorhanden: `{p.path}` („{p.title}“). "
                        f"Nutze `wiki_memory_patch(path=\"{p.path}\", ...)`, damit das Gedächtnis kompakt bleibt."
                    )


async def rebuild_memory_index(db: AsyncSession) -> str:
    """Baut `memory/_index.md` deterministisch aus allen aktiven `memory/`-Seiten auf und verknüpft den Wissensgraphen."""
    rows = (await db.scalars(
        select(Page).where(Page.space == "memory", Page.deleted_at.is_(None)).order_by(Page.importance.desc(), Page.title)
    )).all()
    by_cat: dict[str, list[Page]] = {c: [] for c in MEMORY_CATEGORIES}
    other: list[Page] = []
    by_path_id: dict[str, str] = {}
    for r in rows:
        by_path_id[r.path.lower()] = r.id
        if r.path in SYSTEM_MEMORY_PAGES:
            continue
        parts = r.path.split("/")
        cat = parts[1] if len(parts) >= 3 else ""
        if cat in by_cat:
            by_cat[cat].append(r)
        else:
            other.append(r)

    lines = [
        "# KI-Gedächtnis – Zentraler Wissensindex",
        "",
        "> [!note] Automatisch gepflegtes Inhaltsverzeichnis",
        "> Diese Übersicht listet alle strukturierten Wissensseiten im Bereich `memory/`. "
        "KI-Agenten lesen zuerst diesen Index oder suchen gezielt per `wiki_search` und pflegen bestehende Seiten weiter, "
        "statt lose Einzelnotizen anzulegen. Regeln siehe [[memory/_schema|Struktur-Schema]].",
        "",
    ]
    total = sum(len(v) for v in by_cat.values()) + len(other)
    lines.append(f"**Gesamtbestand:** {total} aktive Wissensseite{'n' if total != 1 else ''}")
    lines.append("")

    for cat in MEMORY_CATEGORIES:
        label, desc = CATEGORY_LABELS[cat]
        items = by_cat[cat]
        lines.append(f"## {label} (`memory/{cat}/`)")
        lines.append(f"*{desc}*")
        lines.append("")
        if not items:
            lines.append("- *(noch keine Einträge)*")
        else:
            for p in items:
                pin = " 📌" if p.pinned else ""
                summ = f" — {p.summary}" if p.summary else ""
                lines.append(f"- [[{p.path}|{p.title}]]{pin}{summ}")
        lines.append("")

    if other:
        lines.append("## Weitere Seiten")
        for p in other:
            lines.append(f"- [[{p.path}|{p.title}]]" + (f" — {p.summary}" if p.summary else ""))
        lines.append("")

    body = "\n".join(lines).strip() + "\n"
    fm = {
        "title": "KI-Gedächtnis – Index",
        "summary": "Automatisch gepflegter Katalog aller Seiten im KI-Gedächtnis",
        "managed": "auto-index",
    }
    full = compose_markdown(fm, body)
    write_vault_file("memory/_index", full)

    idx_page = await db.scalar(select(Page).where(Page.path == "memory/_index"))
    now = utcnow()
    bhash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if idx_page:
        idx_page.title = "KI-Gedächtnis – Index"
        idx_page.summary = fm["summary"]
        idx_page.frontmatter = fm
        idx_page.body_hash = bhash
        idx_page.word_count = len(body.split())
        idx_page.deleted_at = None
        idx_page.updated_at = now
        idx_page.updated_by = "system"
    else:
        idx_page = Page(
            space="memory",
            path="memory/_index",
            folder="memory",
            slug="_index",
            title="KI-Gedächtnis – Index",
            summary=fm["summary"],
            frontmatter=fm,
            body_hash=bhash,
            word_count=len(body.split()),
            importance=5,
            managed_by="auto_index",
            created_by="system",
            updated_by="system",
            created_at=now,
            updated_at=now,
        )
        db.add(idx_page)
        await db.flush()

    await db.execute(text("DELETE FROM pages_fts WHERE page_id = :pid"), {"pid": idx_page.id})
    await db.execute(
        text(
            "INSERT INTO pages_fts (page_id, title, summary, tags_text, headings_text, body) "
            "VALUES (:pid, :title, :summary, :tags, :heads, :body)"
        ),
        {
            "pid": idx_page.id,
            "title": idx_page.title,
            "summary": idx_page.summary,
            "tags": "gedaechtnis index",
            "heads": "KI-Gedächtnis Zentraler Wissensindex",
            "body": body,
        },
    )
    await db.execute(delete(PageLink).where(PageLink.source_page_id == idx_page.id))
    for lk in link_svc.extract_links(body):
        tid = by_path_id.get(lk.target_raw.lower())
        if tid and tid != idx_page.id:
            db.add(PageLink(
                source_page_id=idx_page.id,
                target_page_id=tid,
                target_raw=lk.target_raw,
                alias=lk.alias,
                is_embed=False,
                context_snippet=lk.snippet,
            ))
    return body
