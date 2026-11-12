"""REST-API der Mail-App (Oberfläche unter mail.kulbarts.com; davor gehört Cloudflare Access)."""
import asyncio
import json
import re
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import bus
from .gmail import Gmail, GmailError, NotConnected, build_raw

router = APIRouter(prefix="/api/v1")
_gmail: Gmail | None = None
ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
MAX_UPLOAD = 24 * 1024 * 1024  # Gmail erlaubt 25 MB pro Mail


def gmail() -> Gmail:
    global _gmail
    if _gmail is None:
        _gmail = Gmail()
    return _gmail


def check(*ids: str) -> None:
    for i in ids:
        if not ID.match(i or ""):
            raise HTTPException(400, "Ungültige ID")


async def run(coro):
    """Gmail-Fehler als verständliche HTTP-Antworten."""
    try:
        return await coro
    except NotConnected as e:
        raise HTTPException(409, f"Google ist nicht verbunden: {e}. In IDA unter Einstellungen → Google anmelden.") from e
    except GmailError as e:
        if e.status == 404:
            raise HTTPException(404, f"Gmail: {e.detail}") from e
        if e.status in (429, 503):   # Gmail bremst oder ist kurz weg – die Seite versucht es selbst noch einmal
            raise HTTPException(503, "Gmail ist gerade ausgelastet – gleich nochmal versuchen", headers={"retry-after": "2"}) from e
        raise HTTPException(502, f"Gmail: {e.detail}") from e


@router.get("/health")
async def health():
    return {"ok": True}


@router.get("/me")
async def me():
    g = gmail()
    p = await run(g.profile())
    return {"email": p.get("emailAddress"), "send_as": await run(g.send_as())}


@router.get("/contacts")
async def contacts():
    return await run(gmail().contacts())


@router.get("/labels")
async def labels():
    return await run(gmail().labels())


@router.get("/threads")
async def threads(label: str = "INBOX", q: str = "", page: str = ""):
    if label != "ALL":
        check(label)
    g = gmail()
    return await run(g.threads(label, q[:500], page[:200], g.s.page_size))


@router.get("/threads/{tid}")
async def thread(tid: str):
    check(tid)
    return await run(gmail().thread(tid))


class Modify(BaseModel):
    add: list[str] = []
    remove: list[str] = []


@router.post("/threads/{tid}/modify")
async def modify(tid: str, body: Modify):
    check(tid, *body.add, *body.remove)
    await run(gmail().modify(tid, body.add, body.remove))
    bus.publish({"type": "changed", "thread": tid})
    return {"ok": True}


@router.post("/threads/{tid}/trash")
async def trash(tid: str, undo: bool = False):
    check(tid)
    await run(gmail().trash(tid, undo))
    bus.publish({"type": "changed", "thread": tid})
    return {"ok": True}


@router.get("/messages/{mid}/attachments/{aid}")
async def attachment(mid: str, aid: str, name: str = "anhang", type: str = "application/octet-stream", download: bool = False):
    check(mid, aid)
    data = await run(gmail().attachment(mid, aid))
    safe_type = type if re.match(r"^[\w.+-]+/[\w.+-]+$", type) else "application/octet-stream"
    # nur Bilder/PDF/Text direkt anzeigen; alles andere (auch HTML/SVG) als Download – nie fremdes HTML auf unserer Adresse
    inline = not download and (safe_type.startswith("image/") and safe_type != "image/svg+xml" or safe_type in ("application/pdf", "text/plain"))
    disp = "inline" if inline else "attachment"
    return Response(data, media_type=safe_type, headers={"content-disposition": f"{disp}; filename*=UTF-8''{quote(name[:200])}",
                                                          "cache-control": "private, max-age=86400", "x-content-type-options": "nosniff",
                                                          "content-security-policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"})


async def _files(files: list[UploadFile] | None) -> list[tuple[str, str, bytes]]:
    out, total = [], 0
    for f in files or []:
        data = await f.read()
        total += len(data)
        if total > MAX_UPLOAD:
            raise HTTPException(413, "Anhänge zu groß (Gmail erlaubt zusammen höchstens 25 MB)")
        out.append((f.filename or "anhang", f.content_type or "application/octet-stream", data))
    return out


async def _forwarded(mid: str, skip: str) -> list[tuple[str, str, bytes]]:
    """Beim Weiterleiten die Anhänge der Original-Mail mitnehmen (außer den abgewählten)."""
    if not mid:
        return []
    check(mid)
    g = gmail()
    m = await run(g.get(f"/messages/{mid}", format="full"))
    view = await run(g.message_view(m))
    drop = set(skip.split(",")) if skip else set()
    return [(a["name"], a["type"], await run(g.attachment(mid, a["id"]))) for a in view["attachments"] if a["id"] not in drop]


@router.post("/send")
async def send(to: str = Form(""), cc: str = Form(""), bcc: str = Form(""), subject: str = Form(""), text: str = Form(""),
               sender: str = Form(""), thread_id: str = Form(""), in_reply_to: str = Form(""), references: str = Form(""),
               draft_id: str = Form(""), forward_from: str = Form(""), forward_skip: str = Form(""), draft: bool = Form(False),
               files: list[UploadFile] | None = File(None)):
    if not draft and not (to.strip() or cc.strip() or bcc.strip()):
        raise HTTPException(400, "Empfänger fehlt")
    for v in (to, cc, bcc, sender, subject, in_reply_to):
        if "\n" in v or "\r" in v:
            raise HTTPException(400, "Ungültige Kopfzeile")
    if thread_id:
        check(thread_id)
    if draft_id:
        check(draft_id)
    att = await _files(files) + await _forwarded(forward_from, forward_skip)
    raw = build_raw(sender=sender, to=to, cc=cc, bcc=bcc, subject=subject, text=text, in_reply_to=in_reply_to, references=references, files=att)
    g = gmail()
    if draft:
        d = await run(g.save_draft(raw, thread_id, draft_id))
        bus.publish({"type": "changed"})
        return {"ok": True, "draft_id": d.get("id"), "thread_id": (d.get("message") or {}).get("threadId")}
    r = await run(g.send(raw, thread_id))
    if draft_id:
        try:
            await g.delete_draft(draft_id)
        except (GmailError, NotConnected):
            pass
    bus.publish({"type": "changed", "thread": r.get("threadId")})
    return {"ok": True, "id": r.get("id"), "thread_id": r.get("threadId")}


@router.delete("/drafts/{draft_id}")
async def delete_draft(draft_id: str):
    check(draft_id)
    await run(gmail().delete_draft(draft_id))
    bus.publish({"type": "changed"})
    return {"ok": True}


@router.get("/stream")
async def stream(request: Request):
    """Server-Sent Events: {"type": "changed"} bzw. {"type": "new", "from", "subject"} – die Oberfläche lädt dann neu."""
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), 20)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})
