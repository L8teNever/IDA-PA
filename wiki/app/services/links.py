"""Obsidian-Link-Engine: `[[Wikilinks]]`, `![[Embeds]]`, `#Tags`, Überschriften & automatisches Umschreiben bei Umbenennung."""
import re
from dataclasses import dataclass

from .vault import slugify

_FENCED = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_INLINE_TAG = re.compile(r"(?:^|(?<=\s))#([a-zA-ZäöüÄÖÜß][a-zA-Z0-9äöüÄÖÜß/_-]{0,60})")


@dataclass
class ParsedLink:
    target_raw: str          # Ziel ohne #heading und ohne |alias (z. B. "memory/projekte/homelab" oder "mietvertrag")
    heading: str | None
    alias: str | None
    is_embed: bool
    snippet: str


def _strip_code(md: str) -> str:
    """Ersetzt Codeblöcke durch Leerzeichen gleicher Länge, damit Offsets für Snippets stimmen."""
    s = _FENCED.sub(lambda m: " " * len(m.group(0)), md)
    return _INLINE_CODE.sub(lambda m: " " * len(m.group(0)), s)


def extract_headings(body: str) -> list[dict]:
    clean = _strip_code(body or "")
    out = []
    for m in _HEADING.finditer(clean):
        text = m.group(2).strip()
        out.append({"level": len(m.group(1)), "text": text, "anchor": slugify(text, "abschnitt")})
    return out


def normalize_tag(t: str) -> str:
    s = (t or "").strip().lstrip("#").lower()
    s = re.sub(r"[^a-z0-9äöüß/_-]+", "-", s).strip("-/")
    return s[:70]


def extract_tags(frontmatter: dict | None, body: str) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    raw_fm = (frontmatter or {}).get("tags") or []
    if isinstance(raw_fm, str):
        raw_fm = [x.strip() for x in raw_fm.split(",") if x.strip()]
    if isinstance(raw_fm, list):
        for item in raw_fm:
            nt = normalize_tag(str(item))
            if nt and nt not in seen:
                seen.add(nt)
                tags.append(nt)
    clean = _strip_code(body or "")
    # Überschriftenzeilen beginnen auch mit #, haben aber ein Leerzeichen nach # -> _INLINE_TAG verlangt Buchstaben direkt nach #
    for m in _INLINE_TAG.finditer(clean):
        nt = normalize_tag(m.group(1))
        if nt and nt not in seen:
            seen.add(nt)
            tags.append(nt)
    return tags


def extract_links(body: str) -> list[ParsedLink]:
    clean = _strip_code(body or "")
    raw_body = body or ""
    out: list[ParsedLink] = []
    for m in _WIKILINK.finditer(clean):
        is_embed = m.group(1) == "!"
        inner = m.group(2).strip()
        alias = None
        if "|" in inner:
            inner, alias = inner.split("|", 1)
            inner, alias = inner.strip(), alias.strip() or None
        heading = None
        if "#" in inner:
            inner, heading = inner.split("#", 1)
            inner, heading = inner.strip(), heading.strip() or None
        target = inner.replace("\\", "/").strip("/")
        if target.lower().endswith(".md"):
            target = target[:-3]
        if not target:
            continue
        # Kontext-Ausschnitt aus der umgebenden Zeile
        line_start = raw_body.rfind("\n", 0, m.start()) + 1
        line_end = raw_body.find("\n", m.end())
        if line_end == -1:
            line_end = len(raw_body)
        snippet = raw_body[line_start:line_end].strip()[:260]
        out.append(ParsedLink(target_raw=target[:300], heading=heading[:150] if heading else None,
                              alias=alias[:150] if alias else None, is_embed=is_embed, snippet=snippet))
    return out


def resolve_target(target_raw: str, source_space: str, by_path: dict[str, str], by_slug: dict[str, list[tuple[str, str]]]) -> str | None:
    """Löst `target_raw` auf eine `page_id` auf (Obsidian-Logik):
    1. Exakter Pfad (`privat/01-alltag-wohnen/mietvertrag`)
    2. Relativ zum gleichen Space (`01-alltag-wohnen/mietvertrag` → `<source_space>/01-alltag-wohnen/mietvertrag`)
    3. Suffix-Pfad über alle Spaces
    4. Kurzname / Dateiname (`[[mietvertrag]]`): bevorzugt im selben Space, sonst erster Treffer global.
    Dateianhänge mit Dateiendung (`.png`, `.pdf` usw.) sind keine Seiten-Knoten → `None`, außer es gibt exakt so eine Seite.
    """
    t = target_raw.strip().strip("/")
    low = t.lower()
    if low in by_path:
        return by_path[low]
    scoped = f"{source_space}/{low}"
    if scoped in by_path:
        return by_path[scoped]
    if "/" in low:
        suffix = "/" + low
        for p, pid in by_path.items():
            if p.endswith(suffix):
                return pid
    # Bei offensichtlichen Medien-/Dateiendungen keinen falschen Seiten-Treffer raten
    if "." in t.split("/")[-1] and t.rsplit(".", 1)[-1].lower() in (
        "png", "jpg", "jpeg", "gif", "webp", "svg", "pdf", "zip", "mp3", "mp4", "wav", "ogg", "csv", "xlsx", "docx", "ics"
    ):
        return None
    s = slugify(t.split("/")[-1], "")
    if s and s in by_slug:
        cands = by_slug[s]
        for sp, pid in cands:
            if sp == source_space:
                return pid
        return cands[0][1]
    return None


def rewrite_links_in_body(body: str, old_path: str, old_slug: str, new_path: str) -> tuple[str, int]:
    """Schreibt beim Verschieben/Umbenennen einer Seite alle passenden `[[...]]`-Verweise um (Codeblöcke bleiben unberührt)."""
    changed = 0
    old_low = old_path.lower()
    old_rel = old_low.split("/", 1)[1] if "/" in old_low else old_low

    # Maskiere Codebereiche, damit nichts im Code verändert wird
    protected: list[str] = []

    def hide(m):
        protected.append(m.group(0))
        return f"\x00CODE{len(protected) - 1}\x00"

    masked = _FENCED.sub(hide, body)
    masked = _INLINE_CODE.sub(hide, masked)

    def repl(m):
        nonlocal changed
        bang = m.group(1)
        inner = m.group(2)
        alias_part = ""
        rest = inner
        if "|" in rest:
            rest, al = rest.split("|", 1)
            alias_part = f"|{al}"
        head_part = ""
        if "#" in rest:
            rest, hd = rest.split("#", 1)
            head_part = f"#{hd}"
        clean_t = rest.strip().replace("\\", "/").strip("/")
        if clean_t.lower().endswith(".md"):
            clean_t = clean_t[:-3]
        low_t = clean_t.lower()
        if low_t in (old_low, old_rel) or slugify(low_t, "") == old_slug:
            changed += 1
            return f"{bang}[[{new_path}{head_part}{alias_part}]]"
        return m.group(0)

    updated = _WIKILINK.sub(repl, masked)
    for idx, orig in enumerate(protected):
        updated = updated.replace(f"\x00CODE{idx}\x00", orig)
    return updated, changed
