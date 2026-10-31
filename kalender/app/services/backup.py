"""Backups: nächtlich eine konsistente Kopie der Datenbank (VACUUM INTO) plus ICS-Export, die letzten N bleiben."""
import asyncio
import logging
import sqlite3
from datetime import datetime

from ..config import get_settings
from ..db import session_scope
from ..errors import Invalid, NotFound
from . import ics
from .timeutil import local_now

log = logging.getLogger("kalender.backup")


def _vacuum(src: str, dest: str) -> None:
    con = sqlite3.connect(src, timeout=30)
    try:
        con.execute("VACUUM INTO ?", (dest,))
    finally:
        con.close()


async def run() -> dict:
    s = get_settings()
    stamp = local_now().strftime("%Y%m%d-%H%M%S")
    db_file = s.backup_dir / f"kalender-{stamp}.db"
    db_file.unlink(missing_ok=True)  # zwei Backups in derselben Sekunde: das neuere ersetzt das ältere (VACUUM INTO kennt kein Überschreiben)
    await asyncio.to_thread(_vacuum, str(s.db_path), str(db_file))
    async with session_scope() as db:
        data = await ics.export(db)
    ics_file = s.backup_dir / f"kalender-{stamp}.ics"
    ics_file.write_bytes(data)
    _prune(s.backup_keep)
    log.info("Backup %s", db_file.name)
    return {"name": db_file.name, "size": db_file.stat().st_size}


def _prune(keep: int) -> None:
    s = get_settings()
    for pattern in ("kalender-*.db", "kalender-*.ics"):
        files = sorted(s.backup_dir.glob(pattern))
        for f in files[:-keep] if keep > 0 else files:
            f.unlink(missing_ok=True)


def listing() -> list[dict]:
    s = get_settings()
    out = []
    for f in sorted(s.backup_dir.glob("kalender-*.db"), reverse=True):
        ts = datetime.fromtimestamp(f.stat().st_mtime).astimezone()
        out.append({"name": f.name, "size": f.stat().st_size, "at": ts.isoformat(timespec="seconds"), "has_ics": f.with_suffix(".ics").exists()})
    return out


def path(name: str):
    s = get_settings()
    if "/" in name or "\\" in name or not name.startswith("kalender-") or not name.endswith((".db", ".ics")):
        raise Invalid("Ungültiger Dateiname")
    p = s.backup_dir / name
    if not p.exists():
        raise NotFound("Backup nicht gefunden")
    return p
