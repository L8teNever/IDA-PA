"""Allgemeine Dateianhänge pro Wiki-Seite (`/api/v1/files/<page_id>/<name>`)."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, Response

from ..auth import Caller, caller
from ..db import session_scope
from ..services import files as file_svc

router = APIRouter(prefix="/api/v1", tags=["files"])


@router.post("/files/{page_id}")
async def upload_file(page_id: str, file: UploadFile = File(...), c: Caller = Depends(caller)):
    data = await file.read()
    async with session_scope(write=True) as db:
        att = await file_svc.upload_attachment(
            db,
            c,
            page_id,
            file.filename or "datei",
            file.content_type or "application/octet-stream",
            data,
        )
        return {"attachment": att}


@router.get("/files/{page_id}/{name}")
async def download_file(page_id: str, name: str, request: Request, download: bool = False, c: Caller = Depends(caller)):
    async with session_scope(write=False) as db:
        f_path, att = await file_svc.get_attachment_file(db, c, page_id, name)
        etag = f'"{att.sha256[:20]}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})

        disp_type = "attachment" if download or att.kind in ("archive", "other") else "inline"
        encoded = quote(att.name)
        headers = {
            "ETag": etag,
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"{disp_type}; filename*=UTF-8''{encoded}",
        }
        if att.mime == "image/svg+xml":
            headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        return FileResponse(f_path, media_type=att.mime, headers=headers)


@router.delete("/files/{page_id}/{name}")
async def delete_file(page_id: str, name: str, c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return await file_svc.delete_attachment(db, c, page_id, name)
