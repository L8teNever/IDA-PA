"""Allgemeine Dateianhänge zu Wiki-Seiten (`/data/files/<page_id>/<name>`)."""
import hashlib
import mimetypes
import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Caller
from ..config import get_settings
from ..db import pending, utcnow
from ..errors import Invalid, NotFound
from ..models import Attachment, Revision
from . import permissions as perm
from . import store
from .vault import read_vault_file

_SAFE_NAME = re.compile(r"[^\w.\- äöüÄÖÜß()]+")


def safe_filename(name: str) -> str:
    clean = _SAFE_NAME.sub("_", Path(name or "datei").name).strip(" .")[:140]
    return clean or "datei"


def page_files_dir(page_id: str) -> Path:
    base = get_settings().files_dir
    d = (base / page_id).resolve()
    if not d.is_relative_to(base):
        raise Invalid("Ungültige Seiten-ID")
    return d


def detect_kind_and_mime(name: str, client_mime: str, head: bytes) -> tuple[str, str]:
    if head.startswith(b"\x89PNG"):
        return "image", "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp"
    if head.startswith(b"%PDF"):
        return "pdf", "application/pdf"
    ext = Path(name).suffix.lower()
    guessed, _ = mimetypes.guess_type(name)
    mime = guessed or client_mime or "application/octet-stream"
    if ext == ".svg" or mime == "image/svg+xml":
        return "image", "image/svg+xml"
    if mime.startswith("image/"):
        return "image", mime
    if mime.startswith("audio/") or ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac"):
        return "audio", mime
    if mime.startswith("video/") or ext in (".mp4", ".webm", ".mov", ".mkv"):
        return "video", mime
    if ext in (".zip", ".tar", ".gz", ".7z", ".rar"):
        return "archive", mime
    if mime.startswith("text/") or ext in (
        ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv", ".py", ".js", ".ts",
        ".html", ".css", ".sql", ".xml", ".toml", ".ini", ".log", ".sh", ".ics",
    ):
        return "text", mime if mime != "application/octet-stream" else "text/plain; charset=utf-8"
    return "other", mime


async def upload_attachment(
    db: AsyncSession,
    caller: Caller,
    page_ref: str,
    filename: str,
    client_mime: str,
    data: bytes,
    *,
    source_chat_file_id: str | None = None,
    approved_by: str | None = None,
    approved_proposal: bool = False,
    log_revision: bool = True,
) -> dict:
    max_bytes = get_settings().wiki_max_file_mb * 1024 * 1024
    if not data:
        raise Invalid("Die hochgeladene Datei ist leer.")
    if len(data) > max_bytes:
        raise Invalid(f"Datei ist zu groß (höchstens {get_settings().wiki_max_file_mb} MB).")

    page = await store.get_page_or_404(db, page_ref, caller)
    perm.require_write(caller, page.space, approved_proposal=approved_proposal)

    sha = hashlib.sha256(data).hexdigest()
    if source_chat_file_id:
        existing = await db.scalar(
            select(Attachment).where(Attachment.page_id == page.id, Attachment.source_chat_file_id == source_chat_file_id)
        )
        if existing:
            return _att_dict(page.id, existing)

    d = page_files_dir(page.id)
    d.mkdir(parents=True, exist_ok=True)
    base_name = safe_filename(filename)
    name = base_name
    stem, suf, idx = Path(base_name).stem, Path(base_name).suffix, 1
    while (d / name).exists():
        # Bei exakt gleichem Inhalt während Sync vorhandene Datei wiederverwenden
        if source_chat_file_id and hashlib.sha256((d / name).read_bytes()).hexdigest() == sha:
            break
        idx += 1
        name = f"{stem} ({idx}){suf}"

    (d / name).write_bytes(data)
    kind, mime = detect_kind_and_mime(name, client_mime, data[:4096])
    now = utcnow()
    att = Attachment(
        page_id=page.id,
        name=name,
        mime=mime,
        kind=kind,
        size=len(data),
        sha256=sha,
        source_chat_file_id=source_chat_file_id,
        uploaded_by=caller.by,
        created_at=now,
    )
    db.add(att)
    await db.flush()

    if log_revision:
        last_rev = (await db.scalars(
            select(Revision).where(Revision.page_id == page.id).order_by(Revision.rev_number.desc()).limit(1)
        )).first()
        rev = Revision(
            page_id=page.id,
            rev_number=(last_rev.rev_number + 1) if last_rev else 1,
            action="attach",
            space=page.space,
            path_snapshot=page.path,
            title_snapshot=page.title,
            content_full=read_vault_file(page.path),
            note=f"Datei angehängt: {name}",
            by=caller.by,
            approved_by=approved_by,
            via=caller.via,
            at=now,
        )
        db.add(rev)
        await db.flush()
        page.current_revision_id = rev.id
        page.updated_at = now
        pending(db).append({"type": "changed", "revision_id": rev.id, "page_id": page.id, "path": page.path, "space": page.space, "action": "attach", "by": caller.by})

    return _att_dict(page.id, att)


async def get_attachment_file(db: AsyncSession, caller: Caller, page_ref: str, name: str) -> tuple[Path, Attachment]:
    page = await store.get_page_or_404(db, page_ref, caller)
    clean_name = safe_filename(name)
    att = await db.scalar(select(Attachment).where(Attachment.page_id == page.id, Attachment.name == clean_name))
    f = page_files_dir(page.id) / clean_name
    if not att or not f.is_file():
        raise NotFound("Anhang nicht gefunden")
    return f, att


async def delete_attachment(
    db: AsyncSession,
    caller: Caller,
    page_ref: str,
    name: str,
    *,
    approved_by: str | None = None,
    approved_proposal: bool = False,
) -> dict:
    page = await store.get_page_or_404(db, page_ref, caller)
    perm.require_write(caller, page.space, approved_proposal=approved_proposal)
    clean_name = safe_filename(name)
    att = await db.scalar(select(Attachment).where(Attachment.page_id == page.id, Attachment.name == clean_name))
    if not att:
        raise NotFound("Anhang nicht gefunden")
    f = page_files_dir(page.id) / clean_name
    f.unlink(missing_ok=True)
    await db.delete(att)

    now = utcnow()
    last_rev = (await db.scalars(
        select(Revision).where(Revision.page_id == page.id).order_by(Revision.rev_number.desc()).limit(1)
    )).first()
    rev = Revision(
        page_id=page.id,
        rev_number=(last_rev.rev_number + 1) if last_rev else 1,
        action="detach",
        space=page.space,
        path_snapshot=page.path,
        title_snapshot=page.title,
        content_full=read_vault_file(page.path),
        note=f"Anhang entfernt: {clean_name}",
        by=caller.by,
        approved_by=approved_by,
        via=caller.via,
        at=now,
    )
    db.add(rev)
    await db.flush()
    page.current_revision_id = rev.id
    pending(db).append({"type": "changed", "revision_id": rev.id, "page_id": page.id, "path": page.path, "space": page.space, "action": "detach", "by": caller.by})
    return {"ok": True, "deleted": clean_name, "revision_id": rev.id}


def purge_files_for_page(page_id: str) -> None:
    d = page_files_dir(page_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _att_dict(page_id: str, a: Attachment) -> dict:
    return {
        "id": a.id,
        "page_id": page_id,
        "name": a.name,
        "mime": a.mime,
        "kind": a.kind,
        "size": a.size,
        "uploaded_by": a.uploaded_by,
        "created_at": a.created_at.isoformat(),
        "url": f"/api/v1/files/{page_id}/{a.name}",
    }
