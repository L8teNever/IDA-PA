"""Vollständige Sicherung (`wiki.db` per `VACUUM INTO` + `vault/` + `files/` als ZIP) & portabler Obsidian-Export."""
import io
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import get_settings


def _add_dir_to_zip(zf: zipfile.ZipFile, src: Path, arc_prefix: str) -> None:
    if not src.exists():
        return
    for p in sorted(src.rglob("*")):
        if p.is_file():
            zf.write(p, f"{arc_prefix}/{p.relative_to(src).as_posix()}")


def run_backup() -> dict:
    """Erzeugt ein konsistentes Backup-Archiv unter `/data/backups/wiki-YYYYMMDD-HHMMSS.zip`."""
    s = get_settings()
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo(s.timezone)).strftime("%Y%m%d-%H%M%S")
    dest = s.backup_dir / f"wiki-{stamp}.zip"

    with tempfile.TemporaryDirectory() as tmp:
        snap_db = Path(tmp) / "wiki.db"
        if s.db_path.exists():
            con = sqlite3.connect(str(s.db_path))
            try:
                escaped = snap_db.as_posix().replace("'", "''")
                con.execute(f"VACUUM INTO '{escaped}'")
            finally:
                con.close()

        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if snap_db.exists():
                zf.write(snap_db, "wiki.db")
            _add_dir_to_zip(zf, s.vault_dir, "vault")
            _add_dir_to_zip(zf, s.files_dir, "files")

    # Alte Sicherungen rotieren
    existing = sorted(s.backup_dir.glob("wiki-*.zip"), key=lambda f: f.name, reverse=True)
    for old in existing[max(1, s.backup_keep):]:
        old.unlink(missing_ok=True)

    return {
        "name": dest.name,
        "size": dest.stat().st_size,
        "created_at": datetime.fromtimestamp(dest.stat().st_mtime, tz=ZoneInfo(s.timezone)).isoformat(),
    }


def list_backups() -> list[dict]:
    s = get_settings()
    if not s.backup_dir.exists():
        return []
    tz = ZoneInfo(s.timezone)
    out = []
    for f in sorted(s.backup_dir.glob("wiki-*.zip"), key=lambda x: x.name, reverse=True):
        st = f.stat()
        out.append({
            "name": f.name,
            "size": st.st_size,
            "created_at": datetime.fromtimestamp(st.st_mtime, tz=tz).isoformat(),
        })
    return out


def build_export_zip_bytes() -> bytes:
    """Baut einen sofortigen portablen ZIP-Export aller Markdown-Dateien (`vault/`) und Dateianhänge (`files/`)."""
    s = get_settings()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _add_dir_to_zip(zf, s.vault_dir, "vault")
        _add_dir_to_zip(zf, s.files_dir, "files")
    return buf.getvalue()
