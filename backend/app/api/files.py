"""Files and pictures for chats: upload before sending, show them in the chat (see services/files.py)."""
from urllib.parse import quote

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse

from ..services import files as svc
from ..services.errors import Invalid
from .deps import DB

router = APIRouter(prefix="/api/v1", tags=["files"])


@router.post("/uploads", status_code=201)
async def upload(file: UploadFile, db: DB):
    """One file; send its id with the message (ChatSend.files). Unsent uploads are removed after a day."""
    data = await file.read(max(svc.LIMITS.values()) + 1)
    if len(data) > max(svc.LIMITS.values()):
        raise Invalid(f"„{file.filename}“ ist zu groß")
    return svc.info(await svc.save(db, file.filename or "datei", file.content_type or "", data))


@router.delete("/uploads/{file_id}")
async def remove(file_id: str, db: DB):
    """Takes an attached file out again before sending."""
    await svc.remove_unsent(db, file_id)
    return {"ok": True}


@router.get("/files/{file_id}")
async def get_file(file_id: str, db: DB):
    f = await svc.get(db, file_id)
    return FileResponse(svc.path(f), media_type=f.mime if f.kind != "text" else "text/plain; charset=utf-8",
                        headers={"content-disposition": f"inline; filename*=UTF-8''{quote(f.name)}", "cache-control": "private, max-age=31536000, immutable"})
