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
SRC = re.compile(r"^(mxc|localmxc|beeper-mcp)://[\w./:%@!~+=-]+$")

_beeper: Beeper | None = None
_voice: dict[str, str] = {}


def beeper() -> Beeper:
    global _beeper
    if _beeper is None:
        _beeper = Beeper(get_settings().beeper_mcp_url)
    return _beeper


async def call(fn, *a, **kw):
    try:
        return await fn(*a, **kw)
    except BeeperError as e:
        raise HTTPException(e.status, str(e)) from e


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
                   "size": a.get("fileSize") or 0, "duration": a.get("duration") or 0, "sticker": bool(a.get("isSticker")), "gif": bool(a.get("isGif"))}
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


@router.get("/image")
async def image(src: str):
    check_src(src)
    parts = await call(beeper().content, "get_attachment_image", {"srcURL": src})
    img = next((p for p in parts if p.get("type") == "image" and p.get("data")), None)
    if not img:
        raise HTTPException(404, "Kein Bild")
    return Response(base64.b64decode(img["data"]), media_type=img.get("mimeType") or "image/jpeg",
                    headers={"cache-control": "private, max-age=86400", "content-security-policy": "default-src 'none'", "x-content-type-options": "nosniff"})


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
