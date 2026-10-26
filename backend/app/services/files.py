"""Files and pictures for chats: uploaded first, sent with a message, given to the model.

Pictures and PDFs go to the model as they are (Claude: image/document blocks), text files are put into the
message as text. Other kinds (Word, ZIP …) are refused – the model could not read them.
"""
import shutil
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import utcnow
from ..models import ChatFile
from .errors import Invalid, NotFound

IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log", ".ini", ".toml", ".html", ".htm", ".css",
            ".js", ".ts", ".py", ".java", ".c", ".h", ".cpp", ".cs", ".go", ".rs", ".sh", ".sql", ".ics", ".vcf", ".srt"}
LIMITS = {"image": 5 * 1024 * 1024, "pdf": 25 * 1024 * 1024, "text": 1024 * 1024}  # what the model accepts
MAX_PER_MESSAGE = 10
KEEP_UNSENT = timedelta(days=1)


def folder() -> Path:
    d = get_settings().data_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path(f: ChatFile) -> Path:
    return folder() / f.id


def kind_of(name: str, mime: str, head: bytes) -> tuple[str, str]:
    """(kind, mime) from the content where possible – the browser's type is only a hint."""
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
    if mime.startswith("text/") or Path(name).suffix.lower() in TEXT_EXT:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            if len(head) - e.start > 4:  # not just a character cut off at the end of the sample
                raise Invalid(f"„{name}“ ist keine lesbare Textdatei (UTF-8)") from e
        return "text", "text/plain"
    raise Invalid(f"„{name}“: dieser Dateityp geht nicht – nur Bilder (PNG, JPG, GIF, WebP), PDFs und Textdateien")


async def save(db: AsyncSession, name: str, mime: str, data: bytes) -> ChatFile:
    name = (Path(name or "datei").name or "datei")[:200]
    if not data:
        raise Invalid(f"„{name}“ ist leer")
    kind, mime = kind_of(name, mime or "", data[:4096])
    if len(data) > LIMITS[kind]:
        raise Invalid(f"„{name}“ ist zu groß (höchstens {LIMITS[kind] // (1024 * 1024)} MB für {'Bilder' if kind == 'image' else 'PDFs' if kind == 'pdf' else 'Textdateien'})")
    f = ChatFile(name=name, mime=mime, kind=kind, size=len(data))
    db.add(f)
    await db.flush()
    path(f).write_bytes(data)
    return f


async def get(db: AsyncSession, file_id: str) -> ChatFile:
    f = await db.get(ChatFile, file_id)
    if not f or not path(f).exists():
        raise NotFound("Datei nicht gefunden")
    return f


async def remove_unsent(db: AsyncSession, file_id: str) -> None:
    f = await get(db, file_id)
    if f.chat_id:
        raise Invalid("Die Datei gehört schon zu einem Chat")
    path(f).unlink(missing_ok=True)
    await db.delete(f)


async def take(db: AsyncSession, ids: list[str], chat_id: str) -> list[ChatFile]:
    """Links the uploaded files to the chat (in the order given)."""
    if len(ids) > MAX_PER_MESSAGE:
        raise Invalid(f"Höchstens {MAX_PER_MESSAGE} Dateien pro Nachricht")
    out = []
    for i in dict.fromkeys(ids):
        f = await db.get(ChatFile, str(i))
        if not f or f.chat_id not in (None, chat_id) or not path(f).exists():
            raise Invalid("Eine Datei ist nicht mehr da – bitte nochmal anhängen")
        f.chat_id = chat_id
        out.append(f)
    return out


def info(f: ChatFile) -> dict:
    return {"id": f.id, "name": f.name, "mime": f.mime, "kind": f.kind, "size": f.size}


def as_text(f: ChatFile) -> str:
    return path(f).read_bytes().decode("utf-8", errors="replace")


async def delete_for_chat(db: AsyncSession, chat_id: str) -> None:
    for f in (await db.scalars(select(ChatFile).where(ChatFile.chat_id == chat_id))).all():
        path(f).unlink(missing_ok=True)


async def purge(db: AsyncSession) -> None:
    """Uploads that were never sent (after a day) and files whose chat is gone."""
    for f in (await db.scalars(select(ChatFile).where(ChatFile.chat_id.is_(None), ChatFile.created_at < utcnow() - KEEP_UNSENT))).all():
        path(f).unlink(missing_ok=True)
        await db.delete(f)
    known = set((await db.scalars(select(ChatFile.id))).all())
    for p in folder().iterdir():
        if p.is_file() and p.name not in known and p.stat().st_mtime < (utcnow() - KEEP_UNSENT).timestamp():
            p.unlink(missing_ok=True)
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
