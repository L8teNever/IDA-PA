"""Dateisystem-Vault (`/data/vault/`): Pfad-Normalisierung, YAML-Frontmatter & atomares Schreiben von `.md`-Dateien."""
import os
import re
import unicodedata
from pathlib import Path

import yaml

from ..config import get_settings
from ..errors import Invalid
from ..models import SPACES

_SEG_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)


def slugify(text: str, fallback: str = "seite") -> str:
    """Lesbarer Dateiname aus einem Titel (Umlaute werden zu ae/oe/ue/ss, Kleinbuchstaben, Bindestriche)."""
    t = (text or "").strip()
    for k, v in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "ae"), ("Ö", "oe"), ("Ü", "ue"), ("ß", "ss")):
        t = t.replace(k, v)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9._-]+", "-", t).strip("-._")
    return (t[:100] or fallback)


def normalize_path(raw: str) -> tuple[str, str, str, str]:
    """Normalisiert einen Seitenpfad (z. B. `privat/01-alltag-wohnen/Mietvertrag.md`)
    → `(path, space, folder, slug)` ohne `.md`. Verhindert zuverlässig `..`-Ausbrüche.
    """
    s = (raw or "").strip().replace("\\", "/").strip("/")
    if s.lower().endswith(".md"):
        s = s[:-3]
    parts = [p.strip() for p in s.split("/") if p.strip()]
    if len(parts) < 2:
        raise Invalid("Pfad muss mindestens `<hauptordner>/<seite>` enthalten (z. B. `privat/00-inbox/notiz`)")
    space = parts[0].lower()
    if space not in SPACES:
        raise Invalid(f"Ungültiger Hauptordner „{parts[0]}“ – erlaubt sind: {', '.join(SPACES)}")
    cleaned = [space]
    for seg in parts[1:]:
        if seg in (".", "..") or _SEG_BAD.search(seg):
            raise Invalid(f"Ungültiger Pfadbestandteil: „{seg}“")
        c = seg.strip(" .")
        if not c or len(c) > 140:
            raise Invalid(f"Ungültiger Name im Pfad: „{seg}“")
        cleaned.append(c)
    full = "/".join(cleaned)
    if len(full) > 310:
        raise Invalid("Pfad ist zu lang (höchstens 310 Zeichen)")
    folder = "/".join(cleaned[:-1])
    slug = slugify(cleaned[-1], cleaned[-1].lower())
    # Sicherheitscheck auf dem echten Dateisystem
    vdir = get_settings().vault_dir
    file_path = (vdir / f"{full}.md").resolve()
    if not file_path.is_relative_to(vdir):
        raise Invalid("Pfad liegt außerhalb des Vaults")
    return full, space, folder, slug


def file_for(path: str) -> Path:
    vdir = get_settings().vault_dir
    target = (vdir / f"{path}.md").resolve()
    if not target.is_relative_to(vdir):
        raise Invalid("Pfad außerhalb des Vaults")
    return target


def parse_markdown(raw: str) -> tuple[dict, str]:
    """Zerlegt kompletten Dateiinhalt in `(frontmatter_dict, body_markdown)`."""
    text = (raw or "").replace("\r\n", "\n")
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, m.group(2)


def compose_markdown(frontmatter: dict | None, body: str) -> str:
    """Baut sauberes Markdown mit optionalem YAML-Frontmatter zusammen."""
    clean_body = (body or "").replace("\r\n", "\n").lstrip("\n")
    if clean_body and not clean_body.endswith("\n"):
        clean_body += "\n"
    fm = {k: v for k, v in (frontmatter or {}).items() if v is not None and v != "" and v != []}
    if not fm:
        return clean_body
    dumped = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
    return f"---\n{dumped}\n---\n\n{clean_body}" if clean_body else f"---\n{dumped}\n---\n"


def write_vault_file(path: str, content: str) -> None:
    """Schreibt `.md` atomar über eine temporäre Datei im selben Ordner."""
    target = file_for(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)


def read_vault_file(path: str) -> str:
    target = file_for(path)
    return target.read_text(encoding="utf-8") if target.is_file() else ""


def delete_vault_file(path: str) -> None:
    target = file_for(path)
    target.unlink(missing_ok=True)
    # Leere Unterordner bis unterhalb des Space-Roots aufräumen (nicht die 00..99 Hauptordner löschen)
    vdir = get_settings().vault_dir
    p = target.parent
    while p != vdir and p.parent != vdir and p.parent.parent != vdir:
        try:
            p.rmdir()
        except OSError:
            break
        p = p.parent


def ensure_dirs(paths: list[str]) -> None:
    vdir = get_settings().vault_dir
    for rel in paths:
        d = (vdir / rel).resolve()
        if d.is_relative_to(vdir):
            d.mkdir(parents=True, exist_ok=True)
