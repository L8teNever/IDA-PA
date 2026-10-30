"""Anhänge, QR-Code für den Abo-Link, Web Push und „Ida fragen“."""
import io
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Body, Depends, File, UploadFile
from fastapi.responses import FileResponse, Response

from ..auth import Caller, caller
from ..config import get_settings
from ..db import session_scope
from ..errors import Forbidden, Invalid, NotFound
from ..services import push, store
from ..services import recurrence as rec
from ..services import settings as cfg

router = APIRouter(prefix="/api/v1", tags=["extra"])
MAX_FILE = 20 * 1024 * 1024


# ---------------- Anhänge (liegen unter /data/files/<termin>/) ----------------
def _dir(event_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]{6,16}", event_id):
        raise NotFound("Termin nicht gefunden")
    return get_settings().files_dir / event_id


def _safe(name: str) -> str:
    n = re.sub(r"[^\w.\- äöüÄÖÜß()]+", "_", Path(name or "datei").name).strip(" .")[:120]
    return n or "datei"


async def _event(db, occ_id: str, by: str, write: bool):
    eid = rec.split_id(occ_id)[0]
    return await store.get_event(db, eid, by, write=write)


def _listing(d: Path) -> list[dict]:
    if not d.is_dir():
        return []
    return [{"name": f.name, "size": f.stat().st_size} for f in sorted(d.iterdir(), key=lambda f: f.stat().st_mtime) if f.is_file()]


@router.get("/events/{occ_id}/files")
async def list_files(occ_id: str, c: Caller = Depends(caller)):
    async with session_scope() as db:
        ev = await _event(db, occ_id, c.by, write=False)
    return {"files": _listing(_dir(ev.id))}


@router.post("/events/{occ_id}/files")
async def upload(occ_id: str, file: UploadFile = File(...), c: Caller = Depends(caller)):
    async with session_scope() as db:
        ev = await _event(db, occ_id, c.by, write=True)
    data = await file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise Invalid("Die Datei ist zu groß (höchstens 20 MB)")
    d = _dir(ev.id)
    d.mkdir(parents=True, exist_ok=True)
    name = _safe(file.filename)
    stem, suf, i = Path(name).stem, Path(name).suffix, 1
    while (d / name).exists():
        i += 1
        name = f"{stem} ({i}){suf}"
    (d / name).write_bytes(data)
    return {"files": _listing(d), "name": name}


@router.get("/files/{event_id}/{name}")
async def get_file(event_id: str, name: str, c: Caller = Depends(caller)):
    async with session_scope() as db:
        await _event(db, event_id, c.by, write=False)
    f = _dir(event_id) / _safe(name)
    if not f.is_file():
        raise NotFound("Anhang nicht gefunden")
    return FileResponse(f, filename=f.name)


@router.delete("/files/{event_id}/{name}")
async def delete_file(event_id: str, name: str, c: Caller = Depends(caller)):
    async with session_scope() as db:
        await _event(db, event_id, c.by, write=True)
    f = _dir(event_id) / _safe(name)
    if not f.is_file():
        raise NotFound("Anhang nicht gefunden")
    f.unlink()
    return {"files": _listing(_dir(event_id))}


# ---------------- QR-Code für den Abo-Link ----------------
@router.get("/feed/qr.svg")
async def feed_qr(c: Caller = Depends(caller)):
    if c.by != "user":
        raise Forbidden("Den Abo-Link sieht nur der Nutzer")
    import qrcode
    import qrcode.image.svg
    async with session_scope(write=True) as db:
        token = await cfg.feed_token(db)
    base = get_settings().kalender_public_url.rstrip("/")
    url = f"{base}/ics/{token}.ics" if base else f"/ics/{token}.ics"
    buf = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=2).save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"cache-control": "no-store"})


# ---------------- Web Push (Opt-in) ----------------
@router.get("/push")
async def push_state(c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return {"public_key": await push.public_key(db), "devices": [{"label": s.get("label", ""), "at": s.get("at"), "endpoint": s["endpoint"]} for s in await push.subs(db)]}


@router.post("/push/subscribe")
async def push_subscribe(body: dict = Body(...), c: Caller = Depends(caller)):
    if c.by != "user":
        raise Forbidden("Geräte meldet nur der Nutzer an")
    async with session_scope(write=True) as db:
        return {"ok": True, "devices": await push.subscribe(db, body.get("subscription") or {}, str(body.get("label") or ""))}


@router.post("/push/unsubscribe")
async def push_unsubscribe(body: dict = Body(...), c: Caller = Depends(caller)):
    async with session_scope(write=True) as db:
        return {"ok": True, "devices": await push.unsubscribe(db, str(body.get("endpoint") or ""))}


@router.post("/push/test")
async def push_test(c: Caller = Depends(caller)):
    sent = await push.send([{"type": "test", "title": "IDA Kalender", "body": "So sehen Erinnerungen aus 👋"}], force=True)
    return {"ok": bool(sent), "sent": sent}


# ---------------- Ida fragen (die einzige Stelle, an der der Kalender IDA aufruft) ----------------
@router.post("/ask")
async def ask(body: dict = Body(...), c: Caller = Depends(caller)):
    s = get_settings()
    text = str(body.get("text") or "").strip()
    if not text:
        raise Invalid("Frage fehlt")
    if not (s.ida_internal_url and s.kalender_ida_token):
        raise Invalid("Ida ist nicht verbunden (IDA_INTERNAL_URL / KALENDER_IDA_TOKEN fehlen)")
    ctx = ""
    if body.get("event_id"):
        async with session_scope() as db:
            try:
                ev = await _event(db, str(body["event_id"]), c.by, write=False)
                ctx = f"Es geht um den Termin „{ev.title}“ (id {body['event_id']})."
            except NotFound:
                pass
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=300)) as client:
            r = await client.post(f"{s.ida_internal_url.rstrip('/')}/api/v1/ext/kalender/ask", json={"text": text, "context": ctx},
                                  headers={"Authorization": f"Bearer {s.kalender_ida_token}"})
    except httpx.HTTPError as e:
        raise Invalid("Ida ist gerade nicht erreichbar") from e
    if r.status_code >= 300:
        raise Invalid(r.json().get("detail", "Ida konnte nicht antworten") if r.headers.get("content-type", "").startswith("application/json") else "Ida konnte nicht antworten")
    return r.json()
