"""Dateispeicher: pro Dokument eine .idadocs (JSON mit Einstellungen) und eine .md (reiner Text). Beide werden immer zusammen geschrieben."""
import json
import re
import secrets
import time
from pathlib import Path

from .config import get_settings

FORMAT = "idadocs"
VERSION = 1
# Standard: nur Abweichungen davon landen in der .idadocs (Maße in mm, Schrift in pt)
DEFAULTS = {
    "marginTop": 25, "marginBottom": 20, "marginLeft": 30, "marginRight": 20,
    "font": "Arial", "size": 11, "lineHeight": 1.5, "paraSpacing": 6,
    "align": "justify", "h1": 16, "h2": 14, "h3": 12, "headingNumbers": True,
    "pageNumbers": True, "pageNumberPos": "center",
}
_ID = re.compile(r"^[a-z0-9]{6,20}$")


def new_id() -> str:
    return secrets.token_hex(5)


def valid_id(i: str) -> bool:
    return bool(_ID.match(i or ""))


def diff_settings(raw: dict | None) -> dict:
    """Nur bekannte Schlüssel mit passendem Typ, und nur wenn sie vom Standard abweichen."""
    out = {}
    for k, v in (raw or {}).items():
        d = DEFAULTS.get(k)
        if d is None or isinstance(v, bool) != isinstance(d, bool):
            continue
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            if not isinstance(v, (int, float)) or not 0 <= v <= 500:
                continue
        elif isinstance(d, str) and (not isinstance(v, str) or len(v) > 60):
            continue
        if v != d:
            out[k] = v
    return out


def _paths(i: str) -> tuple[Path, Path]:
    d = get_settings().docs_dir
    return d / f"{i}.idadocs", d / f"{i}.md"


def _atomic(p: Path, text: str) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf8")
    tmp.replace(p)


def save(i: str, title: str, md: str, settings: dict | None, created: float | None = None) -> dict:
    ida, mdp = _paths(i)
    now = time.time()
    if created is None:
        try:
            created = json.loads(ida.read_text(encoding="utf8")).get("created", now)
        except (OSError, ValueError):
            created = now
    doc = {"format": FORMAT, "version": VERSION, "id": i, "title": title.strip() or "Ohne Titel",
           "created": created, "updated": now, "settings": diff_settings(settings), "content": md}
    _atomic(ida, json.dumps(doc, ensure_ascii=False, indent=1))
    _atomic(mdp, md)
    return doc


def load(i: str) -> dict | None:
    try:
        d = json.loads(_paths(i)[0].read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return d if d.get("format") == FORMAT else None


def listing() -> list[dict]:
    out = []
    for p in get_settings().docs_dir.glob("*.idadocs"):
        d = load(p.stem)
        if d:
            text = d.get("content", "")
            out.append({"id": d["id"], "title": d["title"], "updated": d["updated"], "created": d.get("created", d["updated"]),
                        "words": len(text.split()), "preview": re.sub(r"[#>*_`\[\]|-]+", " ", text)[:140].strip()})
    return sorted(out, key=lambda x: -x["updated"])


def delete(i: str) -> bool:
    gone = False
    for p in _paths(i):
        if p.exists():
            p.unlink()
            gone = True
    return gone


def parse_import(name: str, text: str) -> tuple[str, str, dict]:
    """.idadocs (JSON) oder .md → (Titel, Text, Einstellungen)."""
    base = re.sub(r"\.(idadocs|md|markdown|txt)$", "", name or "Import", flags=re.I) or "Import"
    if (name or "").lower().endswith(".idadocs"):
        try:
            d = json.loads(text)
        except ValueError as e:
            raise ValueError("Keine gültige .idadocs-Datei") from e
        if not isinstance(d, dict) or d.get("format") != FORMAT or not isinstance(d.get("content"), str):
            raise ValueError("Keine gültige .idadocs-Datei")
        return str(d.get("title") or base), d["content"], diff_settings(d.get("settings"))
    m = re.match(r"^#\s+(.+)", text)
    return (m.group(1).strip() if m else base), text, {}
