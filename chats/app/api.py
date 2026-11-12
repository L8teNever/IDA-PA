"""Schnittstelle der Chats-App: übersetzt die Beeper-Tools (MCP) für die Oberfläche."""
import asyncio
import base64
import html
import json
import re
import shutil
import time
from urllib.parse import quote

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import bus, store
from .config import get_settings
from .mcp import Beeper, BeeperError

router = APIRouter(prefix="/api/v1")

HEAD = re.compile(r"^## (?P<title>.*) \(chatID: (?P<id>[^)]+)\)\s*$")
NEXT = re.compile(r"Next page \(older\): cursor='([^']+)'")
NET = re.compile(r"^Chat on (?P<net>.+?) \((?P<acc>[^()]+)\) (?:with|in|called)\b|^Chat on (?P<net2>[\w.-]+) (?:with|in|called)\b")
UNREAD = re.compile(r"It has (\d+) unread")
SRC = re.compile(r"^(mxc|localmxc|beeper-mcp)://[\w./:%@!~+=?&-]{1,4000}$")
# Hat Beeper eine Datei schon heruntergeladen, steht statt mxc:// der lokale Pfad auf dem Beeper-Rechner da. Erlaubt ist nur
# Beepers eigener Medienordner – sonst könnte man über den Dienst beliebige Dateien vom Beeper-Rechner holen.
LOCAL = re.compile(r"^file:///[A-Za-z]:/Users/[^/\\]{1,100}/AppData/Roaming/BeeperTexts/media/[\w.@!~+=-]{1,200}(/[\w.@!~+=-]{1,200}){0,4}$")

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


def reactions(m: dict, me: set[str] | None = None) -> list[dict]:
    out: dict[str, dict] = {}
    for r in m.get("reactions") or []:
        if not isinstance(r, dict):
            continue
        k = r.get("reactionKey") or r.get("emoji") or r.get("key")
        if k:
            e = out.setdefault(k, {"key": k, "n": 0, "mine": False})
            e["n"] += 1
            e["mine"] = e["mine"] or bool(r.get("isSender") or r.get("mine") or (me and r.get("participantID") in me))
    return list(out.values())


def seen_by(m: dict, me: set[str], names: dict[str, str]) -> list[dict]:
    """Wer hat meine Nachricht gelesen? Beeper liefert pro Nachricht {Nutzer-ID: Zeitpunkt} (ohne mich selbst)."""
    s = m.get("seen")
    if not m.get("isSender") or not isinstance(s, dict):
        return []
    return sorted(({"id": u, "name": names.get(u, ""), "at": t if isinstance(t, str) else None}
                   for u, t in s.items() if u not in me and t), key=lambda x: x["at"] or "")


HTML_TAG = re.compile(r"</?(?:p|br|a|b|i|em|strong|u|s|del|strike|code|pre|span|div|ul|ol|li|blockquote|h[1-6]|font|mx-reply)(?:\s[^<>]*)?/?>", re.I)
HTML_LINK = re.compile(r"<a\s[^<>]*?href=([\"'])(.*?)\1[^<>]*>(.*?)</a>", re.I | re.S)


def plain(text: str) -> str:
    """Manche Netzwerke (Matrix, Mail-Brücken, Bots) liefern HTML statt Text. Daraus wird lesbarer Text: Absätze und <br> zu
    Zeilenumbrüchen, Links zu ihrer Adresse. Text ohne bekannte HTML-Tags bleibt unverändert."""
    if not text or not HTML_TAG.search(text):
        return text

    def link(m: re.Match) -> str:
        url, label = html.unescape(m[2]).strip(), html.unescape(HTML_TAG.sub("", m[3])).strip()
        if not label or url in label:
            return label or url
        return f"{label} ({url})" if url.lower().startswith("http") else label

    t = re.sub(r"<mx-reply>.*?</mx-reply>", "", text, flags=re.I | re.S)
    t = HTML_LINK.sub(link, t)
    t = re.sub(r"<br\s*/?>\n?", "\n", t, flags=re.I)
    t = re.sub(r"<li(?:\s[^<>]*)?>", "• ", t, flags=re.I)
    t = re.sub(r"</(?:p|div|li|blockquote|h[1-6]|pre|ul|ol)>\n?", "\n", t, flags=re.I)
    t = html.unescape(HTML_TAG.sub("", t))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def media_src(src) -> str:
    """Nur Adressen, die /media auch holen darf (Beepers eigene Dateien) – sonst leer."""
    return src if isinstance(src, str) and (SRC.match(src) or is_local(src)) else ""


def message(m: dict, me: set[str] | None = None, names: dict[str, str] | None = None) -> dict:
    return {
        "read_by": seen_by(m, me or set(), names or {}), "sender_id": m.get("senderID") or "",
        "id": m.get("id"), "at": m.get("timestamp"), "mine": bool(m.get("isSender")), "sender": m.get("senderName") or "",
        "type": (m.get("type") or "TEXT").upper(), "text": plain(m.get("text") or ""), "deleted": bool(m.get("isDeleted")), "edited": bool(m.get("editedTimestamp")),
        "reply_to": m.get("linkedMessageID") or None, "reactions": reactions(m, me),
        "files": [{"src": a.get("srcURL") or a.get("id"), "kind": kind(a), "mime": a.get("mimeType") or "", "name": a.get("fileName") or "",
                   "size": a.get("fileSize") or 0, "duration": a.get("duration") or 0, "w": (a.get("size") or {}).get("width"), "h": (a.get("size") or {}).get("height"),
                   "transcript": ((a.get("transcription") or {}).get("transcription") or "").strip(), "sticker": bool(a.get("isSticker")), "gif": bool(a.get("isGif")), "poster": media_src(a.get("posterImg"))}
                  for a in m.get("attachments") or []],
        # Link-Vorschau (Titel, Bild), wie Beeper sie zeigt
        "links": [{"url": l.get("originalURL") or l.get("url") or "", "title": plain(l.get("title") or "")[:200], "summary": plain(l.get("summary") or "")[:300],
                   "img": media_src(l.get("img")), "w": (l.get("imgSize") or {}).get("width"), "h": (l.get("imgSize") or {}).get("height")}
                  for l in (m.get("links") or [])[:3] if isinstance(l, dict) and str(l.get("url") or "").lower().startswith("http")],
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
    direction = "after" if direction == "after" else "before"
    key = (chat_id, cursor[:300]) if cursor and direction == "before" else None
    if key and (hit := _older.get(key)) and time.monotonic() - hit[0] < OLDER_TTL:
        return hit[1]   # ältere Seiten ändern sich kaum: "Ältere laden" kommt dann sofort
    d = await fetch_messages(chat_id, cursor[:300], direction)
    out = shape_messages(d, chat_id)
    if key:
        if len(_older) > 400:
            _older.clear()
        _older[key] = (time.monotonic(), out)
    return out


OLDER_TTL = 600
_older: dict[tuple[str, str], tuple[float, dict]] = {}


async def fetch_messages(chat_id: str, cursor: str, direction: str) -> dict:
    """Erst die REST-API von Beeper (JSON, ~100 ms), sonst über MCP (gleiche Daten, langsamer)."""
    params = {"cursor": cursor, "direction": direction} if cursor else {}
    try:
        r = await rest_get(f"/v1/chats/{quote(chat_id, safe='')}/messages", **params)
        if r.status_code == 200 and isinstance((d := r.json()), dict) and "items" in d:
            return d
    except (HTTPException, ValueError):
        pass
    args = {"chatID": chat_id, **params}
    try:
        return json.loads(await call(beeper().text, "list_messages", args))
    except ValueError as e:
        raise HTTPException(502, "Beeper: unerwartete Antwort") from e


def shape_messages(d: dict, chat_id: str = "") -> dict:
    raw = d.get("items", [])
    me = {m["senderID"] for m in raw if m.get("isSender") and m.get("senderID")}
    names = {m["senderID"]: m["senderName"] for m in raw if m.get("senderID") and m.get("senderName") and not m.get("isSender")}
    items = [message(m, me, names) for m in raw]
    items.reverse()   # Beeper liefert neueste zuerst; die Seite will alt → neu
    if chat_id:
        items = keep_deleted(chat_id, items)
    return {"items": items, "more": bool(d.get("hasMore")), "oldest": d.get("oldestCursor"), "newest": d.get("newestCursor")}


def keep_deleted(chat_id: str, items: list[dict]) -> list[dict]:
    """Merkt sich alle Nachrichten; bei gelöschten hängt es an, was vorher drinstand ("was"). Für mich Gelöschtes fällt raus."""
    try:
        known = store.recall(chat_id, [m["id"] for m in items if m.get("id")])
        store.remember(chat_id, [m for m in items if not m["deleted"]])
    except Exception:  # noqa: BLE001 – der Merkzettel darf das Laden nie verhindern
        return items
    out = []
    for m in items:
        k = known.get(str(m["id"]))
        if k and k["hidden"]:
            continue
        if m["deleted"]:
            was = k if k and (k["text"] or k["files"]) else m
            m["was"] = {"text": was["text"], "files": was["files"]} if was["text"] or was["files"] else None
            m["text"], m["files"] = "", []
        out.append(m)
    return out


async def archive(chat_id: str) -> None:
    """Neueste Seite eines Chats holen, nur damit sie gemerkt ist (läuft im Hintergrund, siehe live.py)."""
    shape_messages(await fetch_messages(chat_id, "", "before"), chat_id)


@router.delete("/chats/{chat_id}/messages/{msg_id}")
async def delete_message(chat_id: str, msg_id: str, everyone: bool = False):
    """everyone=true: für alle löschen (wenn das Netzwerk es kann), sonst nur bei mir. Geht nur über die REST-API von Beeper."""
    await rest_call("DELETE", f"/v1/chats/{quote(chat_id, safe='')}/messages/{quote(msg_id, safe='')}?forEveryone={'true' if everyone else 'false'}")
    if not everyone:
        store.hide(chat_id, msg_id)
    for k in [k for k in _older if k[0] == chat_id]:
        _older.pop(k, None)
    return {"ok": True}


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


def is_local(src: str) -> bool:
    return bool(LOCAL.match(src)) and ".." not in src


def check_src(src: str) -> None:
    if not (SRC.match(src) or is_local(src)):
        raise HTTPException(400, "Ungültige Adresse")


MEDIA_MAX = 60 * 1024 * 1024     # größer wird nichts geholt
MEDIA_CACHE = 120 * 1024 * 1024  # so viel hält der Dienst im Speicher (zuletzt benutzte zuerst raus)
_media: "dict[str, tuple[bytes, str]]" = {}
_media_size = 0
_media_locks: "dict[str, asyncio.Lock]" = {}
_media_gate = asyncio.Semaphore(6)   # mehrere Bilder gleichzeitig, aber Beeper nicht überrennen


async def fetch_media(src: str, mime: str) -> tuple[bytes, str]:
    if is_local(src):   # lokal bei Beeper: über die REST-API (assets/serve) holen
        global _rest
        if not rest_base():
            raise HTTPException(409, "Beeper ist nicht eingerichtet")
        if _rest is None:
            _rest = httpx.AsyncClient(timeout=20)
        tok = get_settings().beeper_mcp_token
        try:
            r = await _rest.get(rest_base() + "/v1/assets/serve", params={"url": src}, timeout=120,
                                headers={"authorization": f"Bearer {tok}"} if tok else {})
        except httpx.HTTPError as e:
            raise HTTPException(502, "Beeper nicht erreichbar") from e
        if r.status_code != 200 or not r.content:
            raise HTTPException(404, "Keine Datei")
        ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not SAFE_MIME.match(ct):   # Beeper sagt meist nur octet-stream: Typ aus der Nachricht oder aus den ersten Bytes
            ct = mime.split(";")[0].strip().lower() if SAFE_MIME.match(mime or "") else (sniff(r.content) or "application/octet-stream")
        return r.content, ct
    args = {"srcURL": src, "mimeType": mime if re.match(r"^[\w.+-]+/[\w.+;= -]{1,80}$", mime or "") else None}
    parts = await call(beeper().content, "get_attachment_image", args)
    part = next((p for p in parts if p.get("data") and p.get("mimeType")), None)
    if not part:
        raise HTTPException(404, "Keine Datei")
    return base64.b64decode(part["data"]), part["mimeType"]


async def media_bytes(src: str, mime: str) -> tuple[bytes, str]:
    """Holt eine Datei (Bild, Audio, Video) von Beeper – per MCP (mxc://) oder REST (lokale Datei bei Beeper)."""
    global _media_size
    if src in _media:
        _media[src] = _media.pop(src)   # als zuletzt benutzt merken
        return _media[src]
    lock = _media_locks.setdefault(src, asyncio.Lock())   # dieselbe Datei nur einmal holen, verschiedene parallel
    async with lock:
        if src in _media:
            return _media[src]
        async with _media_gate:
            data, ct = await fetch_media(src, mime)
        if len(data) > MEDIA_MAX:
            raise HTTPException(413, "Datei zu groß")
        _media[src] = (data, ct)
        _media_size += len(data)
        while _media_size > MEDIA_CACHE and len(_media) > 1:
            _, (old, _) = next(iter(_media.items()))
            _media_size -= len(old)
            _media.pop(next(iter(_media)))
        _media_locks.pop(src, None)
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


# ---------------- Profilbilder (REST-API von Beeper Desktop; das MCP liefert keine Bilder) ----------------
_info: dict[str, tuple[float, dict]] = {}
_avatars: dict[str, tuple[bytes, str] | None] = {}
INFO_TTL = 3600
_rest: httpx.AsyncClient | None = None


def rest_base() -> str:
    s = get_settings()
    return (s.beeper_api_url or re.sub(r"/v\d+/mcp/?$|/mcp/?$", "", s.beeper_mcp_url)).rstrip("/")


async def rest_get(path: str, **params) -> httpx.Response:
    global _rest
    if not rest_base():
        raise HTTPException(409, "Beeper ist nicht eingerichtet")
    if _rest is None:
        _rest = httpx.AsyncClient(timeout=20)
    tok = get_settings().beeper_mcp_token
    try:
        return await _rest.get(rest_base() + path, params=params, headers={"authorization": f"Bearer {tok}"} if tok else {})
    except httpx.HTTPError as e:
        raise HTTPException(502, "Beeper nicht erreichbar") from e


def file_url(u: str | None) -> str:
    """Beeper gibt Bilder als file:///C:/…, als Windows-Pfad (C:\\…) oder mxc:// an – assets/serve will eine URL."""
    u = (u or "").strip()
    if re.match(r"^[A-Za-z]:\\", u):
        u = "file:///" + u.replace("\\", "/")
    return u if re.match(r"^(file|mxc|localmxc)://", u) else ""


async def chat_info(chat_id: str) -> dict:
    hit = _info.get(chat_id)
    if hit and time.time() - hit[0] < INFO_TTL:
        return hit[1]
    r = await rest_get(f"/v1/chats/{quote(chat_id, safe='')}")
    if r.status_code != 200:
        info = {"img": "", "people": {}}
    else:
        d = r.json()
        people = {p["id"]: {"name": p.get("fullName") or "", "img": file_url(p.get("imgURL")), "self": bool(p.get("isSelf"))}
                  for p in (d.get("participants") or {}).get("items", []) if p.get("id")}
        img = file_url(d.get("imgURL"))
        if not img and d.get("type") == "single":   # Einzelchat: Bild der anderen Person
            img = next((p["img"] for p in people.values() if not p["self"] and p["img"]), "")
        info = {"img": img, "people": people}
    if len(_info) > 1000:
        _info.pop(next(iter(_info)))
    _info[chat_id] = (time.time(), info)
    return info


def sniff(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return ""


async def avatar_bytes(url: str) -> tuple[bytes, str] | None:
    if url in _avatars:
        return _avatars[url]
    r = await rest_get("/v1/assets/serve", url=url)
    out = None
    if r.status_code == 200 and len(r.content) < 5 * 1024 * 1024 and (ct := sniff(r.content)):
        out = (r.content, ct)
    if len(_avatars) > 800:
        _avatars.pop(next(iter(_avatars)))
    _avatars[url] = out
    return out


async def rest_call(method: str, path: str, json_body: dict | None = None) -> dict:
    global _rest
    if not rest_base():
        raise HTTPException(409, "Beeper ist nicht eingerichtet")
    if _rest is None:
        _rest = httpx.AsyncClient(timeout=20)
    tok = get_settings().beeper_mcp_token
    try:
        r = await _rest.request(method, rest_base() + path, json=json_body, headers={"authorization": f"Bearer {tok}"} if tok else {})
    except httpx.HTTPError as e:
        raise HTTPException(502, "Beeper nicht erreichbar") from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("message") or r.text
        except ValueError:
            msg = r.text
        raise HTTPException(502 if r.status_code >= 500 or r.status_code in (401, 403) else 400, f"Beeper: {str(msg)[:200]}")
    return r.json() if r.content else {}


class React(BaseModel):
    key: str = Field(min_length=1, max_length=64)


def check_ids(*ids: str) -> None:
    for i in ids:
        if not re.match(r"^[\w!:.@=+$-]{1,300}$", i or ""):
            raise HTTPException(400, "Ungültige ID")


@router.post("/chats/{chat_id}/messages/{msg_id}/react")
async def react(chat_id: str, msg_id: str, body: React):
    """Reaktion setzen (Emoji) – über die REST-API von Beeper Desktop."""
    check_ids(chat_id, msg_id)
    await rest_call("POST", f"/v1/chats/{quote(chat_id, safe='')}/messages/{quote(msg_id, safe='')}/reactions", {"reactionKey": body.key})
    return {"ok": True}


@router.delete("/chats/{chat_id}/messages/{msg_id}/react")
async def unreact(chat_id: str, msg_id: str, key: str):
    check_ids(chat_id, msg_id)
    if not 0 < len(key) <= 64:
        raise HTTPException(400, "Ungültige Reaktion")
    await rest_call("DELETE", f"/v1/chats/{quote(chat_id, safe='')}/messages/{quote(msg_id, safe='')}/reactions/{quote(key, safe='')}")
    return {"ok": True}


@router.post("/chats/{chat_id}/read")
async def mark_read(chat_id: str):
    """Chat in Beeper als gelesen markieren – damit auch in Beeper und (je nach Netzwerk) beim Absender."""
    check_ids(chat_id)
    await rest_call("POST", f"/v1/chats/{quote(chat_id, safe='')}/read", {})
    return {"ok": True}


# ---------------- Angepinnte Chats (echte Beeper-Pins, also auch in Beeper selbst oben) ----------------
PIN_MAX = 3
PINS_TTL = 60
_pins: tuple[float, list[dict]] | None = None


async def pinned() -> list[dict]:
    """Angepinnte Chats aus der REST-Liste von Beeper (die Textliste über MCP kennt Pins nicht)."""
    global _pins
    if _pins and time.monotonic() - _pins[0] < PINS_TTL:
        return _pins[1]
    r = await rest_get("/v1/chats", limit=200)
    if r.status_code != 200:
        raise HTTPException(502, "Beeper: Chats nicht lesbar")
    out = [{"id": c["id"], "title": (c.get("title") or "").strip(), "network": (c.get("accountID") or c.get("network") or "").lower(),
            "type": c.get("type") or "", "at": c.get("lastActivity") or "", "unread": int(c.get("unreadCount") or 0),
            "muted": bool(c.get("isMuted")), "pinned": True}
           for c in r.json().get("items", []) if c.get("isPinned") and c.get("id")]
    _pins = (time.monotonic(), out)
    return out


@router.get("/pins")
async def pins():
    return {"items": await pinned(), "max": PIN_MAX}


class Pin(BaseModel):
    pinned: bool


@router.post("/chats/{chat_id}/pin")
async def pin(chat_id: str, body: Pin):
    global _pins
    check_ids(chat_id)
    if body.pinned:
        now = await pinned()
        if chat_id not in {c["id"] for c in now} and len(now) >= PIN_MAX:
            raise HTTPException(400, f"Höchstens {PIN_MAX} Chats anpinnen")
    await rest_call("PATCH", f"/v1/chats/{quote(chat_id, safe='')}", {"isPinned": body.pinned})
    _pins = None
    return {"ok": True}


class Unread(BaseModel):
    message_id: str | None = None   # ab dieser Nachricht ungelesen; leer = nur den Chat als ungelesen markieren


@router.post("/chats/{chat_id}/unread")
async def mark_unread(chat_id: str, body: Unread | None = None):
    """Chat in Beeper wieder als ungelesen markieren (optional ab einer bestimmten Nachricht)."""
    check_ids(chat_id)
    mid = body.message_id if body else None
    if mid:
        check_ids(mid)
    chat = await rest_call("POST", f"/v1/chats/{quote(chat_id, safe='')}/unread", {"messageID": mid} if mid else {})
    n = chat.get("unreadCount") if isinstance(chat, dict) else None
    return {"ok": True, "unread": n if isinstance(n, int) else None}


# ---------------- Dateien und Sprachnachrichten senden (REST-API von Beeper Desktop) ----------------
UPLOAD_MAX = 95 * 1024 * 1024   # Cloudflare lässt pro Anfrage höchstens 100 MB durch


async def to_ogg(data: bytes) -> bytes | None:
    """Sprachaufnahme aus dem Browser (WebM/MP4) → Ogg/Opus, das Format, das WhatsApp, Signal & Co. als Sprachnachricht erwarten."""
    if not shutil.which("ffmpeg"):
        return None
    p = await asyncio.create_subprocess_exec("ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "48000",
                                             "-c:a", "libopus", "-b:a", "32k", "-application", "voip", "-f", "ogg", "pipe:1",
                                             stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _ = await asyncio.wait_for(p.communicate(data), 60)
    except asyncio.TimeoutError:
        p.kill()
        return None
    return out if p.returncode == 0 and out else None


def att_type(mime: str, voice: bool) -> str:
    if voice:
        return "voice-note"
    if mime == "image/gif":
        return "gif"
    return next((t for t in ("image", "video", "audio") if mime.startswith(t + "/")), "file")


@router.post("/chats/{chat_id}/upload")
async def upload(chat_id: str, file: UploadFile = File(...), text: str = Form(""), reply_to: str = Form(""),
                 voice: bool = Form(False), duration: float = Form(0)):
    """Bild, Video, Datei oder Sprachnachricht senden: erst zu Beeper hochladen, dann als Anhang verschicken."""
    check_ids(chat_id)
    if reply_to:
        check_ids(reply_to)
    data = await file.read(UPLOAD_MAX + 1)
    if not data:
        raise HTTPException(400, "Leere Datei")
    if len(data) > UPLOAD_MAX:
        raise HTTPException(413, "Datei zu groß (höchstens 95 MB)")
    name = re.sub(r"[\\/\x00-\x1f]", "_", file.filename or "datei")[:200] or "datei"
    mime = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if voice:
        ogg = await to_ogg(data)
        if ogg:
            data, mime, name = ogg, "audio/ogg", "Sprachnachricht.ogg"
    out = await send_attachment(chat_id, data, name, mime, att_type(mime, voice), duration=duration, text=text, reply_to=reply_to)
    return {"ok": True, "id": out.get("pendingMessageID"), "type": att_type(mime, voice)}


async def send_attachment(chat_id: str, data: bytes, name: str, mime: str, kind_: str, duration: float = 0, text: str = "", reply_to: str = "") -> dict:
    """Datei zu Beeper hochladen und als Anhang in einen Chat schicken."""
    global _rest
    if not rest_base():
        raise HTTPException(409, "Beeper ist nicht eingerichtet")
    if _rest is None:
        _rest = httpx.AsyncClient(timeout=20)
    tok = get_settings().beeper_mcp_token
    try:
        r = await _rest.post(rest_base() + "/v1/assets/upload", files={"file": (name, data, mime)}, data={"fileName": name, "mimeType": mime},
                             headers={"authorization": f"Bearer {tok}"} if tok else {}, timeout=300)
    except httpx.HTTPError as e:
        raise HTTPException(502, "Beeper nicht erreichbar") from e
    up = r.json() if r.status_code == 200 else {}
    if not up.get("uploadID"):
        raise HTTPException(502, f"Beeper: Hochladen ging nicht ({(up.get('error') or r.text)[:150]})")
    att: dict = {"uploadID": up["uploadID"], "type": kind_, "mimeType": mime, "fileName": name}
    if duration and 0 < duration < 36000:
        att["duration"] = round(duration, 2)
    body: dict = {"attachment": att}
    if text.strip():
        body["text"] = text.strip()[:8000]
    if reply_to:
        body["replyToMessageID"] = reply_to
    return await rest_call("POST", f"/v1/chats/{quote(chat_id, safe='')}/messages", body)


# ---------------- Bearbeiten und Weiterleiten ----------------
class Edit(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.put("/chats/{chat_id}/messages/{msg_id}")
async def edit(chat_id: str, msg_id: str, body: Edit):
    """Eigene Textnachricht bearbeiten (Beeper: nur Text, keine Nachrichten mit Anhang; ob es geht, entscheidet das Netzwerk)."""
    check_ids(chat_id, msg_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Leere Nachricht")
    await rest_call("PUT", f"/v1/chats/{quote(chat_id, safe='')}/messages/{quote(msg_id, safe='')}", {"text": text})
    for k in [k for k in _older if k[0] == chat_id]:   # ältere Seiten dieses Chats neu holen, sonst stünde dort der alte Text
        _older.pop(k, None)
    return {"ok": True}


class FwdFile(BaseModel):
    src: str = Field(max_length=4000)
    mime: str = Field("", max_length=120)
    name: str = Field("", max_length=200)
    kind: str = Field("file", max_length=10)
    duration: float = 0
    sticker: bool = False
    gif: bool = False


class Forward(BaseModel):
    text: str = Field("", max_length=8000)
    files: list[FwdFile] = Field(default_factory=list, max_length=10)


EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "video/mp4": "mp4", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a"}


@router.post("/chats/{chat_id}/forward")
async def forward(chat_id: str, body: Forward):
    """Nachricht in einen anderen Chat weiterleiten. Beeper kennt kein echtes Weiterleiten: Text und Anhänge werden dort neu gesendet
    (die Dateien holt der Dienst direkt bei Beeper, sie laufen nicht übers Handy)."""
    check_ids(chat_id)
    text = body.text.strip()
    if not text and not body.files:
        raise HTTPException(400, "Nichts zum Weiterleiten")
    for f in body.files:
        check_src(f.src)
    for i, f in enumerate(body.files):
        data, ct = await media_bytes(f.src, f.mime)
        mime = ct if ct != "application/octet-stream" else (f.mime.split(";")[0].strip().lower() or ct)
        voice = f.kind == "audio" and "ogg" in mime
        t = "sticker" if f.sticker else "gif" if f.gif else att_type(mime, voice)
        name = re.sub(r"[\\/\x00-\x1f]", "_", f.name)[:200] or f"datei.{EXT.get(mime, 'bin')}"
        await send_attachment(chat_id, data, name, mime, t, duration=f.duration, text=text if i == 0 else "")
    if text and not body.files:
        await rest_call("POST", f"/v1/chats/{quote(chat_id, safe='')}/messages", {"text": text})
    return {"ok": True}


@router.get("/avatar/{chat_id}")
async def avatar(chat_id: str, user: str = ""):
    """Profilbild eines Chats (Gruppenbild bzw. Bild der anderen Person) oder – mit user – eines Gruppenmitglieds."""
    if not re.match(r"^[\w!:.@=+-]{1,300}$", chat_id) or len(user) > 300:
        raise HTTPException(400, "Ungültige ID")
    info = await chat_info(chat_id)
    url = (info["people"].get(user) or {}).get("img", "") if user else info["img"]
    got = await avatar_bytes(url) if url else None
    if not got:
        return Response(status_code=404, headers={"cache-control": "private, max-age=3600"})
    return Response(got[0], media_type=got[1], headers={"cache-control": "private, max-age=86400", "x-content-type-options": "nosniff"})


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
