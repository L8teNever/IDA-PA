"""Schnittstelle der Chats-App: übersetzt die Beeper-Tools (MCP) für die Oberfläche."""
import asyncio
import base64
import json
import re

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import bus
from .config import get_settings
from .mcp import Beeper, BeeperError

router = APIRouter(prefix="/api/v1")

HEAD = re.compile(r"^## (?P<title>.*) \(chatID: (?P<id>[^)]+)\)\s*$")
NEXT = re.compile(r"Next page \(older\): cursor='([^']+)'")
NET = re.compile(r"^Chat on (?P<net>.+?) \((?P<acc>[^()]+)\) (?:with|in|called)\b|^Chat on (?P<net2>[\w.-]+) (?:with|in|called)\b")
UNREAD = re.compile(r"It has (\d+) unread")
SRC = re.compile(r"^(mxc|localmxc|beeper-mcp)://[\w./:%@!~+=?&-]{1,4000}$")

_beeper: Beeper | None = None
_voice: dict[str, str] = {}


def beeper() -> Beeper:
    global _beeper
    if _beeper is None:
        st = get_settings()
        _beeper = Beeper(st.beeper_mcp_url, st.beeper_mcp_token)
    return _beeper


async def call(fn, *a, **kw):
    try:
        return await fn(*a, **kw)
    except BeeperError as e:
        raise HTTPException(502 if e.status == 403 else e.status, str(e)) from e


def parse_chats(text: str) -> tuple[list[dict], str | None]:
    out: list[dict] = []
    for line in text.splitlines():
        if (m := HEAD.match(line)):
            out.append({"id": m["id"], "title": m["title"].strip(), "network": "", "type": "", "at": "", "unread": 0, "muted": False})
        elif out:
            c = out[-1]
            if line.startswith("**Type**:"):
                c["type"] = line.split(":", 1)[1].strip()
            elif line.startswith("**Last Activity**:"):
                c["at"] = line.split(":", 1)[1].strip()
            elif line.startswith("Chat on") and not c["network"]:
                n = NET.match(line)
                if n:
                    c["network"] = (n["acc"] or n["net2"] or "").lower()
                if (u := UNREAD.search(line)):
                    c["unread"] = int(u.group(1))
            elif "It has" in line and (u := UNREAD.search(line)):
                c["unread"] = int(u.group(1))
            elif line.strip() == "This chat is muted.":
                c["muted"] = True
    nxt = NEXT.search(text)
    return out, nxt.group(1) if nxt else None


def kind(a: dict) -> str:
    mime, t = (a.get("mimeType") or "").lower(), (a.get("type") or "").lower()
    if a.get("isVoiceNote") or mime.startswith("audio/") or t in ("audio", "voice"):
        return "audio"
    if mime.startswith("video/") or t == "video" or a.get("isGif"):
        return "video"
    if mime.startswith("image/") or t in ("img", "image"):
        return "image"
    return "file"


def reactions(m: dict) -> list[dict]:
    out: dict[str, dict] = {}
    for r in m.get("reactions") or []:
        if not isinstance(r, dict):
            continue
        k = r.get("reactionKey") or r.get("emoji") or r.get("key")
        if k:
            e = out.setdefault(k, {"key": k, "n": 0, "mine": False})
            e["n"] += 1
            e["mine"] = e["mine"] or bool(r.get("isSender") or r.get("mine"))
    return list(out.values())


def message(m: dict) -> dict:
    return {
        "id": m.get("id"), "at": m.get("timestamp"), "mine": bool(m.get("isSender")), "sender": m.get("senderName") or "",
        "type": (m.get("type") or "TEXT").upper(), "text": m.get("text") or "", "deleted": bool(m.get("isDeleted")),
        "reply_to": m.get("linkedMessageID") or None, "reactions": reactions(m),
        "files": [{"src": a.get("srcURL") or a.get("id"), "kind": kind(a), "mime": a.get("mimeType") or "", "name": a.get("fileName") or "",
                   "size": a.get("fileSize") or 0, "duration": a.get("duration") or 0, "w": (a.get("size") or {}).get("width"), "h": (a.get("size") or {}).get("height"),
                   "transcript": ((a.get("transcription") or {}).get("transcription") or "").strip(), "sticker": bool(a.get("isSticker")), "gif": bool(a.get("isGif"))}
                  for a in m.get("attachments") or []],
    }


@router.get("/health", include_in_schema=False)
async def health():
    return {"ok": True}


@router.get("/chats")
async def chats(q: str = "", unread: bool = False, inbox: str = "primary", cursor: str = ""):
    s = get_settings()
    args = {"limit": s.page_size, "query": q.strip()[:100], "unreadOnly": True if unread else None,
            "inbox": inbox if inbox in ("primary", "low-priority", "archive") else None}
    if cursor:
        args.update(cursor=cursor[:300], direction="before")
    items, nxt = parse_chats(await call(beeper().text, "search_chats", args))
    return {"items": items, "next": nxt}


@router.get("/chats/{chat_id}/messages")
async def messages(chat_id: str, cursor: str = "", direction: str = "before"):
    args = {"chatID": chat_id}
    if cursor:
        args.update(cursor=cursor[:300], direction="after" if direction == "after" else "before")
    try:
        d = json.loads(await call(beeper().text, "list_messages", args))
    except ValueError as e:
        raise HTTPException(502, "Beeper: unerwartete Antwort") from e
    items = [message(m) for m in d.get("items", [])]
    items.reverse()   # Beeper liefert neueste zuerst; die Seite will alt → neu
    return {"items": items, "more": bool(d.get("hasMore")), "oldest": d.get("oldestCursor"), "newest": d.get("newestCursor")}


class Send(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    reply_to: str | None = None


@router.post("/chats/{chat_id}/send")
async def send(chat_id: str, body: Send):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Leere Nachricht")
    out = await call(beeper().text, "send_message", {"chatID": chat_id, "text": text, "replyToMessageID": body.reply_to})
    return {"ok": True, "info": out[:300]}


def check_src(src: str) -> None:
    if not SRC.match(src):
        raise HTTPException(400, "Ungültige Adresse")


MEDIA_MAX = 60 * 1024 * 1024     # größer wird nichts geholt
MEDIA_CACHE = 120 * 1024 * 1024  # so viel hält der Dienst im Speicher (zuletzt benutzte zuerst raus)
_media: "dict[str, tuple[bytes, str]]" = {}
_media_size = 0
_media_lock = asyncio.Lock()


async def media_bytes(src: str, mime: str) -> tuple[bytes, str]:
    """Holt eine Datei (Bild, Audio, Video) von Beeper; get_attachment_image liefert sie als Binärteil."""
    global _media_size
    if src in _media:
        _media[src] = _media.pop(src)   # als zuletzt benutzt merken
        return _media[src]
    async with _media_lock:
        if src in _media:
            return _media[src]
        args = {"srcURL": src, "mimeType": mime if re.match(r"^[\w.+-]+/[\w.+;= -]{1,80}$", mime or "") else None}
        parts = await call(beeper().content, "get_attachment_image", args)
        part = next((p for p in parts if p.get("data") and p.get("mimeType")), None)
        if not part:
            raise HTTPException(404, "Keine Datei")
        data = base64.b64decode(part["data"])
        if len(data) > MEDIA_MAX:
            raise HTTPException(413, "Datei zu groß")
        _media[src] = (data, part["mimeType"])
        _media_size += len(data)
        while _media_size > MEDIA_CACHE and len(_media) > 1:
            _, (old, _) = next(iter(_media.items()))
            _media_size -= len(old)
            _media.pop(next(iter(_media)))
        return _media[src]


SAFE_MIME = re.compile(r"^(image/(png|jpe?g|gif|webp|avif|heic)|audio/[\w.+-]+|video/[\w.+-]+)(;.*)?$", re.I)


@router.get("/media")
async def media(request: Request, src: str, mime: str = ""):
    """Bild, Audio oder Video einer Nachricht – mit Range-Unterstützung (Spulen, iPhone-Wiedergabe)."""
    check_src(src)
    data, ctype = await media_bytes(src, mime)
    if not SAFE_MIME.match(ctype):   # alles andere (HTML, SVG …) nur als Download, nie im Browser ausführen
        ctype = "application/octet-stream"
    headers = {"cache-control": "private, max-age=86400", "content-security-policy": "default-src 'none'", "x-content-type-options": "nosniff",
               "accept-ranges": "bytes"}
    if ctype == "application/octet-stream":
        headers["content-disposition"] = "attachment"
    rng = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("range", ""))
    if rng and (rng[1] or rng[2]):
        n = len(data)
        start, end = (int(rng[1]), int(rng[2]) if rng[2] else n - 1) if rng[1] else (max(0, n - int(rng[2])), n - 1)
        end = min(end, n - 1)
        if start > end:
            return Response(status_code=416, headers={"content-range": f"bytes */{n}"})
        return Response(data[start:end + 1], status_code=206, media_type=ctype, headers={**headers, "content-range": f"bytes {start}-{end}/{n}"})
    return Response(data, media_type=ctype, headers=headers)


@router.get("/image", include_in_schema=False)
async def image(request: Request, src: str):
    return await media(request, src, "")


@router.get("/transcript")
async def transcript(src: str):
    """Sprachnachricht als Text (lokales Whisper-Modell in Beeper)."""
    check_src(src)
    if src not in _voice:
        text = (await call(beeper().text, "transcribe_voice_message", {"srcURL": src})).strip()
        if len(_voice) > 300:
            _voice.pop(next(iter(_voice)))
        _voice[src] = text
    return {"text": _voice[src]}


@router.get("/stream")
async def stream(request: Request):
    """Server-Sent Events: {"type": "chat", "id", "title", "new"} wenn sich ein Chat geändert hat, {"type": "list"} bei Sammeländerungen."""
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
