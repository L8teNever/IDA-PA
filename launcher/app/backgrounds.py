"""Hintergrundbilder des Launchers: liegen im Volume (/data/backgrounds), gelten für alle Geräte gleich."""
import os
import re
import secrets
from pathlib import Path

from .config import get_settings

MAX_BYTES = 12 * 1024 * 1024
MAX_COUNT = 40
_ID = re.compile(r"^[0-9a-f]{16}\.(jpg|png|webp|gif)$")
_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}


def _dir() -> Path:
    d = get_settings().data_dir / "backgrounds"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sniff(data: bytes) -> str | None:
    """Dateityp an den ersten Bytes erkennen (nicht dem Header des Absenders trauen)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def valid(name: str) -> bool:
    return bool(_ID.match(name))


def content_type(name: str) -> str:
    return _TYPES[name.rsplit(".", 1)[1]]


def ids() -> list[str]:
    return sorted(p.name for p in _dir().iterdir() if valid(p.name))


def path(name: str) -> Path | None:
    p = _dir() / name
    return p if valid(name) and p.is_file() else None


def add(data: bytes) -> str:
    ext = sniff(data)
    if not ext:
        raise ValueError("Kein gültiges Bild (JPG, PNG, WebP oder GIF).")
    if len(data) > MAX_BYTES:
        raise ValueError("Bild ist zu groß (max. 12 MB).")
    if len(ids()) >= MAX_COUNT:
        raise ValueError(f"Mehr als {MAX_COUNT} Bilder sind nicht möglich.")
    name = f"{secrets.token_hex(8)}.{ext}"
    tmp = _dir() / f"{name}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, _dir() / name)
    return name


def remove(name: str) -> bool:
    p = path(name)
    if not p:
        return False
    p.unlink()
    return True
