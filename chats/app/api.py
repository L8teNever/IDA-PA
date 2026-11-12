"""Schnittstelle der Chats-App: übersetzt die Beeper-Tools (MCP) für die Oberfläche."""
import asyncio
import base64
import difflib
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


MENTION_URL = re.compile(r"^https://matrix\.to/#/[@!]", re.I)
MD_MENTION = re.compile(r"\[([^\]\n]{1,80})\]\(https://matrix\.to/#/[@!][^)\s]+\)")


def plain(text: str) -> str:
    """Manche Netzwerke (Matrix, Mail-Brücken, Bots) liefern HTML statt Text. Daraus wird lesbarer Text: Absätze und <br> zu
    Zeilenumbrüchen, Links zu ihrer Adresse. Text ohne bekannte HTML-Tags bleibt unverändert."""
    if text and "matrix.to/#/" in text:   # Erwähnung als Markdown-Link
        text = MD_MENTION.sub(lambda m: m[1] if m[1].startswith("@") else f"@{m[1]}", text)
    if not text or not HTML_TAG.search(text):
        return text

    def link(m: re.Match) -> str:
        url, label = html.unescape(m[2]).strip(), html.unescape(HTML_TAG.sub("", m[3])).strip()
        if MENTION_URL.match(url):   # Erwähnung (Matrix-Pill): als "@Name"
            return label if label.startswith("@") else f"@{label}"
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
        # Erwähnungen: wen (IDs) – und ob du gemeint bist (auch @room = alle)
        "mentions": [x for x in m.get("mentions") or [] if isinstance(x, str)][:50],
        "at_me": any(x == "@room" or x in (me or set()) for x in m.get("mentions") or []),
        "files": [{"src": a.get("srcURL") or a.get("id"), "kind": kind(a), "mime": a.get("mimeType") or "", "name": a.get("fileName") or "",
                   "size": a.get("fileSize") or 0, "duration": a.get("duration") or 0, "w": (a.get("size") or {}).get("width"), "h": (a.get("size") or {}).get("height"),
                   "transcript": ((a.get("transcription") or {}).get("transcription") or "").strip(), "sticker": bool(a.get("isSticker")), "gif": bool(a.get("isGif")), "poster": media_src(a.get("posterImg"))}
                  for a in m.get("attachments") or []],
        # Link-Vorschau (Titel, Bild), wie Beeper sie zeigt
        "links": uniq_links([{"url": l.get("originalURL") or l.get("url") or "", "title": plain(l.get("title") or "")[:200], "summary": plain(l.get("summary") or "")[:300],
                   "img": media_src(l.get("img")), "w": (l.get("imgSize") or {}).get("width"), "h": (l.get("imgSize") or {}).get("height")}
                  for l in (m.get("links") or [])[:6] if isinstance(l, dict) and str(l.get("url") or "").lower().startswith("http")])[:3],
    }


def _link_key(u: str) -> str:
    u = re.sub(r"^https?://(www\.)?", "", (u or "").strip().lower()).split("#")[0]
    return u.rstrip("/")


def uniq_links(links: list[dict]) -> list[dict]:
    """Beeper liefert denselben Link manchmal doppelt (kurze und lange Adresse, mit/ohne www) – jede Vorschau nur einmal."""
    out, seen = [], set()
    for l in links:
        k = {_link_key(l["url"])} | ({(l["title"], l["img"])} if l["title"] else set())
        if k & seen:
            continue
        seen |= k
        out.append(l)
    return out


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
    try:
        prev = {k: c.get("preview") for c in await rest_chats() for k in (c.get("localChatID"), c.get("id")) if k}
    except HTTPException:
        prev = {}
    for c in items:
        c["last"] = preview(prev.get(c["id"]), c.get("type") == "group")
    return {"items": items, "next": nxt}


_chats_cache: tuple[float, list[dict]] | None = None


async def rest_chats() -> list[dict]:
    """Die neuesten 200 Chats aus der REST-API (Vorschau, Pins) – 5 s gemerkt, die Liste lädt oft hintereinander."""
    global _chats_cache
    if _chats_cache and time.monotonic() - _chats_cache[0] < 5:
        return _chats_cache[1]
    r = await rest_get("/v1/chats", limit=200)
    if r.status_code != 200:
        raise HTTPException(502, "Beeper: Chats nicht lesbar")
    _chats_cache = (time.monotonic(), [c for c in r.json().get("items", []) if isinstance(c, dict)])
    return _chats_cache[1]


def preview(p, group: bool) -> dict | None:
    if not isinstance(p, dict):
        return None
    text = re.sub(r"\s+", " ", plain(p.get("text") or "")).strip()[:140]
    return {"text": text, "mine": bool(p.get("isSender")), "sender": "" if p.get("isSender") or not group else (p.get("senderName") or "").split(" ")[0][:40],
            "at": p.get("timestamp") or ""}


@router.get("/chats/{chat_id}/messages")
async def messages(chat_id: str, cursor: str = "", direction: str = "before"):
    if (members := group_members(chat_id)):
        return await group_messages(members, cursor if direction == "before" else "")
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
    raw = [m for m in d.get("items", []) if (m.get("type") or "").upper() != "REACTION"]
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


# ---------------- Suche im Chat (Text, Medien, Tag; bei zusammengeführten Chats auf Wunsch nur ein Netzwerk) ----------------
MEDIA_KINDS = ("image", "video", "link", "file")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


@router.get("/chats/{chat_id}/search")
async def search_chat(chat_id: str, q: str = "", media: str = "", start: str = "", end: str = "", only: str = "", cursor: str = ""):
    """Nachrichten eines Chats suchen. media: image/video/link/file (Komma), start/end: ISO-Zeit (z. B. ein Tag), only: ein Chat
    der Zusammenführung. Pro Chat höchstens 20 je Seite; cursor gilt pro Chat (bei Zusammenführung zusammen kodiert)."""
    check_ids(chat_id)
    ids = group_members(chat_id) or [chat_id]
    if only:
        if only not in ids:
            raise HTTPException(400, "Chat gehört nicht dazu")
        ids = [only]
    kinds = [k for k in media.split(",") if k in MEDIA_KINDS]
    base: dict = {"limit": 20, "excludeLowPriority": "false"}
    if q.strip():
        base["query"] = q.strip()[:100]
    if kinds:
        base["mediaTypes"] = kinds
    for k, v in (("dateAfter", start), ("dateBefore", end)):
        if v:
            if not ISO.match(v):
                raise HTTPException(400, "Ungültiges Datum")
            base[k] = v
    if not (q.strip() or kinds or start or end):
        return {"items": [], "next": ""}
    curs = _dec(cursor) if cursor else {}
    todo = [c for c in ids if not cursor or curs.get(c)]

    async def one(c: str) -> tuple[str, dict]:
        params = {**base, "chatIDs": [c], **({"cursor": curs[c][:300], "direction": "before"} if curs.get(c) else {})}
        r = await rest_get("/v1/messages/search", **params)
        if r.status_code != 200:
            raise HTTPException(502, "Beeper: Suche ging nicht")
        return c, r.json()

    got = await asyncio.gather(*(one(c) for c in todo))
    items, nxt = [], {}
    for c, d in got:
        raw = [m for m in d.get("items", []) if isinstance(m, dict) and (m.get("type") or "").upper() != "REACTION"]
        me = {m["senderID"] for m in raw if m.get("isSender") and m.get("senderID")}
        for m in raw:
            x = message(m, me)
            x["chat"] = c
            items.append(x)
        if d.get("hasMore") and d.get("oldestCursor"):
            nxt[c] = d["oldestCursor"]
    items.sort(key=lambda x: x["at"] or "", reverse=True)   # neueste zuerst
    return {"items": items, "next": _enc(nxt) if nxt else ""}


# ---------------- Zusammengeführte Chats (nur hier in IDA Chats, Beeper weiß davon nichts) ----------------
def group_members(chat_id: str) -> list[str]:
    try:
        g = store.groups().get(chat_id)
    except Exception:  # noqa: BLE001
        return []
    return [m["id"] for m in g] if g else []


def _enc(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode()


def _dec(c: str) -> dict:
    try:
        d = json.loads(base64.urlsafe_b64decode(c.encode()))
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


async def group_messages(members: list[str], cursor: str) -> dict:
    """Alle Chats der Gruppe als ein Verlauf. Jede Nachricht trägt ihren Chat ("chat") und die echte ID ("mid");
    "id" ist chat~id, weil IDs nur pro Chat eindeutig sind. Der Cursor merkt sich pro Chat, wie weit zurück geladen ist."""
    cur = _dec(cursor[:8000]) if cursor else {c: "" for c in members}
    todo = [c for c in members if c in cur]

    async def one(c: str):
        try:
            return c, shape_messages(await fetch_messages(c, cur[c][:300], "before"), c)
        except HTTPException:
            return c, None
    pages = await asyncio.gather(*(one(c) for c in todo))
    if all(p is None for _, p in pages) and pages:
        raise HTTPException(502, "Beeper: Chats nicht lesbar")
    items, nxt = [], {}
    for c, p in pages:
        if not p:
            continue
        for m in p["items"]:
            m["chat"], m["mid"], m["id"] = c, m["id"], f"{c}~{m['id']}"
            if m.get("reply_to"):
                m["reply_to"] = f"{c}~{m['reply_to']}"
        items += p["items"]
        if p["more"] and p["oldest"]:
            nxt[c] = p["oldest"]
    items.sort(key=lambda m: m.get("at") or "")
    return {"items": items, "more": bool(nxt), "oldest": _enc(nxt) if nxt else None, "newest": None}


class MergeChat(BaseModel):
    id: str = Field(min_length=1, max_length=300)
    title: str = Field("", max_length=300)
    network: str = Field("", max_length=100)


class Merge(BaseModel):
    chats: list[MergeChat] = Field(min_length=2, max_length=10)


@router.get("/merges")
async def merges():
    meta = store.metas()
    return {"items": [{"id": g, "chats": m, **meta.get(g, {"name": "", "avatar": "", "send": ""})} for g, m in store.groups().items()]}


class MergeMeta(BaseModel):
    name: str | None = Field(None, max_length=100)
    avatar: str | None = Field(None, max_length=300)   # "" = erster Chat
    send: str | None = Field(None, max_length=300)     # "" = automatisch (Chat der letzten eingehenden Nachricht)


@router.patch("/merges/{grp}")
async def merge_settings(grp: str, body: MergeMeta):
    g = store.groups().get(grp)
    if not g:
        raise HTTPException(404, "Keine zusammengeführten Chats")
    ids = {m["id"] for m in g}
    for v in (body.avatar, body.send):
        if v and v not in ids:
            raise HTTPException(400, "Chat gehört nicht dazu")
    store.set_meta(grp, name=body.name.strip() if body.name is not None else None, avatar=body.avatar, send=body.send)
    return {"ok": True}


@router.post("/merges")
async def merge(body: Merge):
    check_ids(*[c.id for c in body.chats])
    return {"ok": True, "id": store.merge([c.model_dump() for c in body.chats])}


@router.delete("/merges/{grp}")
async def unmerge(grp: str, chat: str = ""):
    check_ids(grp, *([chat] if chat else []))
    store.split(grp, chat or None)
    return {"ok": True}


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


class Mention(BaseModel):
    id: str = Field(max_length=300)
    name: str = Field(max_length=120)


class Send(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    reply_to: str | None = None
    mentions: list[Mention] = Field(default_factory=list, max_length=50)   # beim Tippen per @ ausgewählt


def with_mentions(text: str, mentions: list[Mention]) -> str:
    """"@Name" im Text wird zur echten Erwähnung (Matrix-Link als Markdown; Beeper macht daraus die Erwähnung im jeweiligen Netzwerk)."""
    for m in sorted(mentions, key=lambda x: -len(x.name)):
        name = m.name.strip()
        if not name or not re.match(r"^@[\w.=+/-]+:[\w.-]+$", m.id):
            continue
        label = re.sub(r"[\[\]()\\]", "", name)
        text = re.sub(rf"(?<![\w\[])@{re.escape(name)}(?![\w])", lambda _: f"[{label}](https://matrix.to/#/{m.id})", text)
    return text


@router.post("/chats/{chat_id}/send")
async def send(chat_id: str, body: Send):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Leere Nachricht")
    rich = with_mentions(text, body.mentions) if body.mentions else text
    if rich != text:   # mit Erwähnung: über die REST-API (nimmt Markdown an)
        check_ids(chat_id)
        out = await rest_call("POST", f"/v1/chats/{quote(chat_id, safe='')}/messages",
                              {"text": rich, **({"replyToMessageID": body.reply_to} if body.reply_to else {})})
        return {"ok": True, "info": str(out)[:300]}
    out = await call(beeper().text, "send_message", {"chatID": chat_id, "text": text, "replyToMessageID": body.reply_to})
    return {"ok": True, "info": out[:300]}


class FixCtx(BaseModel):
    who: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=1000)


class Fix(BaseModel):
    text: str = Field(max_length=8000)
    context: list[FixCtx] = Field(default_factory=list, max_length=20)   # letzte Nachrichten des Chats (älteste zuerst)


_fix: httpx.AsyncClient | None = None
_engine = {"v": {"engine": "haiku", "vorfilter": True, "kontext": True, "kontext_n": 5}, "t": 0.0}


async def fix_options() -> dict:
    """Einstellungen aus IDA (Engine, Vorab-Check, Kontext); 30 s gemerkt."""
    url = get_settings().ida_url.rstrip("/")
    if not url:
        return {**_engine["v"], "engine": "t5"}
    if time.time() - _engine["t"] > 30:
        _engine["t"] = time.time()
        try:
            r = await _fix.get(f"{url}/api/v1/korrektur/settings", timeout=5)
            if r.status_code == 200:
                _engine["v"] = {**_engine["v"], **{k: v for k, v in r.json().items() if k in _engine["v"]}}
        except httpx.HTTPError:
            pass
    return _engine["v"]


_spell: list | None = None
WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)
SKIP = re.compile(r"https?://\S+|www\.\S+|\S+@\S+|@\S+|:[a-z_]+:")


def _dicts() -> list:
    global _spell
    if _spell is None:
        from spellchecker import SpellChecker   # Wörterbücher Deutsch + Englisch, einmal geladen
        _spell = [SpellChecker(language="de", distance=1), SpellChecker(language="en", distance=1)]
    return _spell


def looks_ok(text: str) -> bool:
    """Schneller Vorab-Check (wenige ms): stehen alle Wörter im deutschen oder englischen Wörterbuch, braucht es keine KI.
    "Hi wie geht's" geht so gar nicht erst an Haiku. Kurze Wörter (Kürzel wie "hdl", "ok") zählen als bekannt."""
    words = [w for w in WORD.findall(SKIP.sub(" ", text)) if len(w) > 3]
    if not words:
        return True
    ds = _dicts()
    for w in words:
        lw = w.lower().replace("’", "'")
        if any(v in d for d in ds for v in (lw, lw.replace("'", ""), lw.split("'")[0])):   # geht's, gibt's
            continue
        if w[0].isupper() and w[1:].islower() and len(w) > 4:   # Namen, Orte, zusammengesetzte Hauptwörter: nicht anmeckern lassen
            if any(d.known([lw[:i]]) and d.known([lw[i:]]) for d in ds for i in range(3, len(lw) - 2)):
                continue
        return False
    return True


def word_parts(old: str, new: str) -> list[dict]:
    """Geänderte Wörter markieren (wie der T5-Dienst: [{t, neu}])."""
    a, b = re.split(r"(\s+)", old), re.split(r"(\s+)", new)
    out: list[dict] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        t, neu = "".join(b[j1:j2]), op != "equal"
        if t:
            out.append({"t": t, "neu": neu})
    return out


async def fix_haiku(text: str, context: list | None = None) -> dict | None:
    url = get_settings().ida_url.rstrip("/")
    if not url:
        return None
    try:
        r = await _fix.post(f"{url}/api/v1/korrektur/haiku", json={"text": text, "context": context or []}, timeout=30)
        d = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None
    new = (d.get("text") or "").strip()
    # Nur Tippfehler: ändert Haiku den Text stark (anderes Wort, Satz umgebaut), geht kein Vorschlag raus
    if not d.get("ok") or not new or difflib.SequenceMatcher(None, text, new, autojunk=False).ratio() < 0.75:
        return None
    return {"changed": new != text, "text": new, "parts": word_parts(text, new), "engine": "haiku"}


async def fix_t5(text: str, context: list | None = None) -> dict | None:
    url = get_settings().korrektur_url.rstrip("/")
    if not url:
        return None
    try:
        r = await _fix.post(f"{url}/api/v1/korrigieren", json={"text": text})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    d = r.json()
    return {"changed": bool(d.get("changed")), "text": d.get("text", ""), "parts": d.get("parts", []), "engine": "t5"}


@router.post("/korrektur")
async def korrektur(body: Fix):
    """Tippfehler-Vorschlag: die in IDA gewählte Engine zuerst (Haiku oder lokales T5), bei Fehler/Limit die andere. Nichts erreichbar: kein Vorschlag."""
    global _fix
    if not body.text.strip():
        return {"changed": False}
    if _fix is None:
        _fix = httpx.AsyncClient(timeout=20)
    o = await fix_options()
    eng = o["engine"]
    if eng == "aus":
        return {"changed": False}
    if o.get("vorfilter", True):
        try:
            if await asyncio.to_thread(looks_ok, body.text):
                return {"changed": False, "skipped": True}
        except Exception:  # noqa: BLE001 – ohne Wörterbuch einfach die KI fragen
            pass
    n = int(o.get("kontext_n") or 5)
    ctx = [c.model_dump() for c in body.context if c.text.strip()][-n:] if o.get("kontext", True) else []
    order = (fix_haiku, fix_t5) if eng == "haiku" else (fix_t5, fix_haiku)
    for f in order:
        d = await f(body.text, ctx)
        if d is not None:
            return d
    return {"changed": False}


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
        info = {"img": img, "people": people, "group": d.get("type") == "group"}
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
    for c in group_members(chat_id) or [chat_id]:   # zusammengeführt: alle Chats der Gruppe
        await rest_call("POST", f"/v1/chats/{quote(c, safe='')}/read", {})
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
    # Die Chatliste (MCP) nutzt die lokale ID ("66736"), REST die Matrix-ID – lokal nehmen, sonst steht der Chat doppelt da
    out = [{"id": str(c.get("localChatID") or c["id"]), "title": (c.get("title") or "").strip(), "network": (c.get("accountID") or c.get("network") or "").lower(),
            "type": c.get("type") or "", "at": c.get("lastActivity") or "", "unread": int(c.get("unreadCount") or 0),
            "muted": bool(c.get("isMuted")), "pinned": True, "last": preview(c.get("preview"), c.get("type") == "group")}
           for c in await rest_chats() if c.get("isPinned") and c.get("id")]
    _pins = (time.monotonic(), out)
    return out


@router.get("/pins")
async def pins():
    return {"items": await pinned(), "max": PIN_MAX}


class Pin(BaseModel):
    pinned: bool


@router.post("/chats/{chat_id}/pin")
async def pin(chat_id: str, body: Pin):
    global _pins, _chats_cache
    check_ids(chat_id)
    if body.pinned:
        now = await pinned()
        if chat_id not in {c["id"] for c in now} and len(now) >= PIN_MAX:
            raise HTTPException(400, f"Höchstens {PIN_MAX} Chats anpinnen")
    await rest_call("PATCH", f"/v1/chats/{quote(chat_id, safe='')}", {"isPinned": body.pinned})
    _pins = _chats_cache = None
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


# ---------------- Info-Seite (wie "Kontaktinfo" bei WhatsApp) ----------------
async def about_one(chat_id: str) -> dict:
    r = await rest_get(f"/v1/chats/{quote(chat_id, safe='')}")
    if r.status_code != 200:
        raise HTTPException(404 if r.status_code == 404 else 502, "Chat nicht gefunden")
    d = r.json()
    people = [{"id": p["id"], "name": (p.get("fullName") or "").strip(), "phone": p.get("phoneNumber") or "", "username": p.get("username") or "",
               "email": p.get("email") or "", "admin": bool(p.get("isAdmin")), "self": bool(p.get("isSelf")), "bot": bool(p.get("isNetworkBot"))}
              for p in (d.get("participants") or {}).get("items", []) if isinstance(p, dict) and p.get("id")]
    return {"id": chat_id, "title": (d.get("title") or "").strip(), "network": d.get("network") or d.get("accountID") or "",
            "description": plain(d.get("description") or "")[:2000], "type": d.get("type") or "", "muted": bool(d.get("isMuted")),
            "archived": bool(d.get("isArchived")), "pinned": bool(d.get("isPinned")), "expiry": d.get("messageExpirySeconds") or 0,
            "total": int((d.get("participants") or {}).get("total") or len(people)), "people": people[:300]}


@router.get("/chats/{chat_id}/about")
async def about(chat_id: str):
    """Kontakt- bzw. Gruppeninfo; bei zusammengeführten Chats eine Info pro Chat."""
    check_ids(chat_id)
    ids = group_members(chat_id) or [chat_id]
    got = await asyncio.gather(*(about_one(c) for c in ids), return_exceptions=True)
    chats = [g for g in got if isinstance(g, dict)]
    if not chats:
        raise HTTPException(502, "Beeper: Info nicht lesbar")
    return {"chats": chats}


# Felder (x, y, b, h) im 100×100-Bild: 2 nebeneinander, 3 = eins links + zwei rechts, 4 = Viertel (wie bei WhatsApp/Signal)
TILES = {2: [(0, 0, 50, 100), (50, 0, 50, 100)], 3: [(0, 0, 50, 100), (50, 0, 50, 50), (50, 50, 50, 50)],
         4: [(0, 0, 50, 50), (50, 0, 50, 50), (0, 50, 50, 50), (50, 50, 50, 50)]}


async def group_collage(info: dict) -> tuple[bytes, str] | None:
    """Gruppe ohne eigenes Bild: aus den Profilbildern von bis zu 4 Mitgliedern ein Bild (SVG, die Bilder stecken darin)."""
    urls = [p["img"] for p in info["people"].values() if not p["self"] and p["img"]][:8]
    got = [g for g in await asyncio.gather(*(avatar_bytes(u) for u in urls), return_exceptions=True) if isinstance(g, tuple)]
    got = [g for g in got if len(g[0]) < 400 * 1024][:4]
    if len(got) < 2:
        return got[0] if got else None
    parts = []
    for (x, y, w, h), (b, ct) in zip(TILES[len(got)], got):
        href = f"data:{ct};base64,{base64.b64encode(b).decode()}"
        parts.append(f'<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="xMidYMid slice" href="{href}"/>')
    lines = '<path d="M50 0V100" stroke="#000" stroke-opacity=".35" stroke-width="1.2"/>' + (
        '<path d="M50 50H100" stroke="#000" stroke-opacity=".35" stroke-width="1.2"/>' if len(got) == 3 else
        '<path d="M0 50H100" stroke="#000" stroke-opacity=".35" stroke-width="1.2"/>' if len(got) == 4 else "")
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">{"".join(parts)}{lines}</svg>'
    return svg.encode(), "image/svg+xml"


@router.get("/avatar/{chat_id}")
async def avatar(chat_id: str, user: str = ""):
    """Profilbild eines Chats (Gruppenbild bzw. Bild der anderen Person) oder – mit user – eines Gruppenmitglieds."""
    if not re.match(r"^[\w!:.@=+-]{1,300}$", chat_id) or len(user) > 300:
        raise HTTPException(400, "Ungültige ID")
    info = await chat_info(chat_id)
    url = (info["people"].get(user) or {}).get("img", "") if user else info["img"]
    got = await avatar_bytes(url) if url else None
    if not got and not user and info.get("group"):
        got = await group_collage(info)
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
