"""Chat „Ida“: ein eigener Chat in der Liste, in dem du direkt mit Ida (dem Haupt-Agenten von IDA) schreibst. Dahinter steckt ein
normaler IDA-Chat (beim ersten Senden angelegt, ID in chats.db). Dazu kommen Idas Benachrichtigungen an dich (IDA-Postfach: Erinnerungen,
notify_user, Antworten anderer Agenten) – sie stehen im selben Verlauf und melden sich am Handy wie eine Chat-Nachricht.
„An Ida schicken“ (langes Drücken auf eine Nachricht) schickt ihr die Nachricht mit dem Chat und den Nachrichten davor als Kontext.

Die Routen hängen vor denen von api.py: /chats/ida/... landet hier, alle anderen Chats gehen wie bisher an Beeper. Siehe docs/CHATS.md."""
import asyncio
import base64
import json
import logging
import mimetypes
import re
import time

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from . import bus, push
from .api import bytes_response
from .config import get_settings

log = logging.getLogger("chats.ida")
router = APIRouter(prefix="/api/v1", tags=["ida"])
IDA = "ida"   # Chat-ID in der App (Beeper-IDs sehen ganz anders aus: "!…:beeper.local")
PREFIX = "ida:"
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
OPT_RE = re.compile(r"\[(?:OPTIONEN|BUTTONS|AUSWAHL)\s*:\s*([^\]]+)\]", re.I)
_c: httpx.AsyncClient | None = None
_agent: dict = {"t": 0.0, "v": None}
_running: dict[str, float] = {}   # läuft gerade eine Antwort? (seit wann)

CHAT_HINT = (
    "\n\n[Hinweis für dich: Falls dir zu genannten Personen, Gruppen, Lehrkräften oder Themen Kontext fehlt, "
    "schau selbstständig in deinem Gedächtnis (memory_search) und im Wiki (wiki_search, wiki_read) nach, ohne zu fragen. "
    "Speichere neue wichtige Erkenntnisse über Personen, Pläne oder Vorlieben im Gedächtnis (memory_save) bzw. Wiki (wiki_memory_save). "
    "Wenn es um einen Termin oder eine Verabredung geht, prüfe den Kalender (calendar_events / calendar_conflicts) und trage ihn ein oder frag kurz nach. "
    "Wenn du Simon eine Rückfrage stellst oder eine Auswahl anbietest, hänge ganz am Ende deiner Antwort "
    "[OPTIONEN: Ja, eintragen | Nein] (oder passende kurze Knöpfe, getrennt durch |) an, damit er direkt tippen kann.]"
)


def enabled() -> bool:
    return bool(get_settings().ida_url.strip())


def client() -> httpx.AsyncClient:
    global _c
    if _c is None:
        _c = httpx.AsyncClient(base_url=get_settings().ida_url.rstrip("/"), timeout=20)
    return _c


async def ida(method: str, path: str, **kw) -> dict | list:
    if not enabled():
        raise HTTPException(404, "Ida ist hier nicht verbunden (IDA_URL leer)")
    try:
        r = await client().request(method, path, **kw)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ida nicht erreichbar: {e.__class__.__name__}") from e
    if r.status_code >= 400:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, f"Ida: {r.text[:200]}")
    return r.json()


async def agent() -> dict:
    """Der Haupt-Agent (Name, Bild) – 5 Minuten gemerkt."""
    if _agent["v"] and time.time() - _agent["t"] < 300:
        return _agent["v"]
    rows = await ida("GET", "/api/v1/agents")
    a = next((x for x in rows if x.get("is_main")), rows[0] if rows else None) if isinstance(rows, list) else None
    if not a:
        raise HTTPException(502, "Ida: kein Agent gefunden")
    _agent.update(t=time.time(), v=a)
    return a


def chat_id() -> str:
    return push._kv("ida_chat")


def custom_avatar() -> tuple[bytes, str] | None:
    raw = push._kv("ida_avatar")
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return base64.b64decode(d["data"]), d.get("mime") or "image/png"
    except (ValueError, KeyError):
        return None


# ---------------- Text: Idas Markdown wird lesbarer Klartext (die App zeigt keine Formatierung) ----------------
def plain(t: str) -> str:
    t = re.sub(r"```[a-z]*\n?", "", t or "")
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m[1] or m[2], t)
    t = re.sub(r"`([^`\n]+)`", r"\1", t)
    t = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", lambda m: m[2] if m[1] == m[2] else f"{m[1]} ({m[2]})", t)
    t = re.sub(r"^(\s*)[-*]\s+", r"\1• ", t, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _clean_user_text(t: str) -> str:
    t = (t or "").strip()
    if CHAT_HINT.strip() in t:
        t = t.replace(CHAT_HINT, "").replace(CHAT_HINT.strip(), "").strip()
    t = re.sub(r"\n*\[Hinweis für dich:[\s\S]*$", "", t).strip()
    if t.startswith("↪ Aus dem Chat ") and "\n\n— Kontext für dich —" in t:
        t = t.split("\n\n— Kontext für dich —", 1)[0].strip()
    return t


def _extract_options(t: str) -> tuple[str, list[str]]:
    opts: list[str] = []
    for m in OPT_RE.finditer(t or ""):
        for part in m[1].split("|"):
            p = part.strip().strip("„“\"'")[:60]
            if p and p not in opts:
                opts.append(p)
    cleaned = OPT_RE.sub("", t or "").strip()
    if not opts and cleaned.endswith("?"):
        last_line = cleaned.rsplit("\n", 1)[-1].lower()
        if any(w in last_line for w in ("soll ich", "möchtest du", "willst du", "eintragen", "anlegen", "speichern", "einrichten", "bestätigen", "passen")):
            opts = ["Ja", "Nein"]
    return cleaned, opts[:4]


def _file_entry(f: dict) -> dict | None:
    fid = str(f.get("id") or "")
    if not FILE_ID.match(fid):
        return None
    name = (f.get("name") or "Datei")[:200]
    mime = f.get("mime") or mimetypes.guess_type(name)[0] or "application/octet-stream"
    kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "audio" if mime.startswith("audio/") else "file"
    return {"src": PREFIX + fid, "kind": kind, "mime": mime, "name": name, "size": int(f.get("size") or 0),
            "duration": 0, "w": None, "h": None, "transcript": "", "sticker": False, "gif": mime == "image/gif", "poster": ""}


def _msg(mid: str, at: str, text: str, mine: bool, sender: str, **extra) -> dict:
    """Gleiche Form wie api.message(), damit die Seite Ida-Nachrichten wie alle anderen zeichnet."""
    return {"id": mid, "at": at, "mine": mine, "sender": sender, "sender_id": "" if mine else IDA, "type": "TEXT", "text": text,
            "deleted": False, "edited": False, "reply_to": None, "reactions": [], "mentions": [], "at_me": False, "files": [], "links": [],
            "read_by": [], **extra}


def _z(iso: str | None) -> str:
    """IDA liefert Zeiten teils ohne Zone (UTC) – für die Sortierung neben den Meldungen einheitlich mit Z."""
    s = str(iso or "")
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s if not s or s.endswith("Z") or re.search(r"[+-]\d\d:\d\d$", s) else s + "Z"


async def history() -> tuple[list[dict], bool]:
    """(Nachrichten des IDA-Chats, läuft gerade eine Antwort?)"""
    cid = chat_id()
    if not cid:
        return [], False
    try:
        d = await ida("GET", f"/api/v1/chats/{cid}")
    except HTTPException as e:
        if e.status_code == 404:   # in IDA gelöscht: beim nächsten Senden neu anlegen
            push._kv_set("ida_chat", "")
            return [], False
        raise
    name = (await agent()).get("name") or "Ida"
    out = []
    last_kind = "chat"
    for m in (d.get("messages") or [])[-150:]:
        if m.get("role") not in ("user", "assistant"):
            continue
        mine = m["role"] == "user"
        raw_files = ((m.get("meta") or {}).get("files") or []) if isinstance(m.get("meta"), dict) else []
        files = [x for f in raw_files if isinstance(f, dict) and (x := _file_entry(f))]
        raw_text = (m.get("text") or "").strip()
        if not raw_text and not files:
            continue
        if mine:
            text = _clean_user_text(raw_text)
            is_fwd = text.startswith("↪ Aus dem Chat ")
            kind = "forward" if is_fwd else "chat"
            last_kind = kind
            opts: list[str] = []
        else:
            text, opts = _extract_options(plain(raw_text))
            kind = last_kind
        out.append(_msg(f"c{m['id']}", _z(m.get("at")), text, mine, "" if mine else name,
                        ida_kind=kind, files=files, type="MEDIA" if files else "TEXT", **({"options": opts} if opts else {})))
    return out, bool(d.get("running"))


async def notices(limit: int = 60) -> tuple[list[dict], int]:
    """Benachrichtigungen an dich aus dem IDA-Postfach (neueste zuerst) und wie viele davon ungelesen sind."""
    d = await ida("GET", "/api/v1/messages/inbox", params={"limit": limit})
    return [x for x in d.get("items") or [] if isinstance(x, dict)], int(d.get("unread") or 0)


def notice_msg(n: dict) -> dict:
    title, raw = (n.get("title") or "").strip(), plain(n.get("text") or "")
    text, opts = _extract_options(f"{title}\n{raw}" if title and title not in raw else raw)
    return _msg(f"n{n['id']}", _z(n.get("at")), text, False, n.get("from_name") or "Ida",
                notice=True, ida_kind="notice", **({"options": opts} if opts else {}))


# ---------------- Ungelesen: Meldungen ungelesen im Postfach + Antworten nach dem letzten Lesen ----------------
def seen_at() -> str:
    return push._kv("ida_seen")


async def row() -> dict:
    """Die Zeile für die Chat-Liste."""
    a = await agent()
    msgs, running = await history()
    ns, unread_n = await notices(20)
    all_ = sorted(msgs + [notice_msg(n) for n in ns], key=lambda m: m["at"])
    seen = seen_at()
    unread = unread_n + sum(1 for m in msgs if not m["mine"] and m["at"] > seen)
    last = all_[-1] if all_ else None
    last_txt = (last["text"] or (f"📎 {last['files'][0]['name']}" if last.get("files") else "")) if last else ""
    return {"id": IDA, "title": a.get("name") or "Ida", "network": "ida", "type": "single", "ida": True, "unread": unread, "running": running,
            "at": last["at"] if last else "", "muted": push.muted(IDA),
            "last": {"text": re.sub(r"\s+", " ", last_txt)[:140], "mine": last["mine"], "sender": "", "at": last["at"]} if last else None}


@router.get("/ida")
async def ida_row():
    if not enabled():
        return {"on": False}
    try:
        return {"on": True, "chat": await row()}
    except HTTPException as e:
        return {"on": True, "chat": None, "error": e.detail}


@router.get(f"/chats/{IDA}/messages")
async def ida_messages(cursor: str = "", direction: str = "before"):
    if cursor:   # alles kommt auf einer Seite
        return {"items": [], "more": False, "oldest": None, "newest": None}
    msgs, running = await history()
    ns, _ = await notices(60)
    items = sorted(msgs + [notice_msg(n) for n in ns], key=lambda m: m["at"])
    if running or IDA in _running:
        items.append(_msg("typing", items[-1]["at"] if items else "", "Ida schreibt …", False, (await agent()).get("name") or "Ida", typing=True))
    return {"items": items, "more": False, "oldest": None, "newest": None}


@router.post(f"/chats/{IDA}/read")
async def ida_read():
    push._kv_set("ida_seen", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".999999Z")
    try:
        await ida("POST", "/api/v1/messages/read-all")
    except HTTPException as e:
        log.info("Ida gelesen: %s", e.detail)
    asyncio.create_task(push.dismiss(IDA))
    bus.publish({"type": "chat", "id": IDA, "title": "Ida", "new": False, "unread": 0})
    return {"ok": True}


@router.get(f"/chats/{IDA}/about")
async def ida_about():
    a = await agent()
    return {"chats": [{"id": IDA, "title": a.get("name") or "Ida", "network": "ida", "group": False, "people": [],
                       "about": a.get("description") or "Deine persönliche Assistentin in IDA", "ida": True,
                       "custom_avatar": custom_avatar() is not None,
                       "ida_url": "https://ida.kulbarts.com" + (f"/chats/{chat_id()}" if chat_id() else "")}]}


@router.get(f"/avatar/{IDA}")
async def ida_avatar():
    if custom := custom_avatar():
        return Response(custom[0], media_type=custom[1],
                        headers={"cache-control": "private, no-cache", "x-content-type-options": "nosniff"})
    try:
        a = await agent()
        if a.get("avatar"):
            r = await client().get(f"/api/v1/agents/{a['id']}/avatar")
            if r.status_code == 200:
                return Response(r.content, media_type=r.headers.get("content-type", "image/png"),
                                headers={"cache-control": "private, max-age=3600", "x-content-type-options": "nosniff"})
    except (HTTPException, httpx.HTTPError):
        pass
    return Response(status_code=404, headers={"cache-control": "private, max-age=600"})


@router.put(f"/avatar/{IDA}")
async def put_ida_avatar(file: UploadFile = File(...)):
    data = await file.read(5 * 1024 * 1024 + 1)
    if not data or len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "Bild fehlt oder ist größer als 5 MB")
    mime = file.content_type if file.content_type in ("image/png", "image/jpeg", "image/webp", "image/gif") else "image/png"
    push._kv_set("ida_avatar", json.dumps({"mime": mime, "data": base64.b64encode(data).decode()}))
    bus.publish({"type": "list"})
    return {"ok": True}


@router.delete(f"/avatar/{IDA}")
async def delete_ida_avatar():
    push._kv_set("ida_avatar", "")
    bus.publish({"type": "list"})
    return {"ok": True}


# ---------------- Dateien im Ida-Chat (Upload & Anzeige) ----------------
async def file_bytes(src: str) -> bytes:
    fid = src.removeprefix(PREFIX)
    if not FILE_ID.match(fid):
        raise HTTPException(400, "Ungültige Datei-ID")
    try:
        r = await client().get(f"/api/v1/files/{fid}", timeout=60)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ida nicht erreichbar: {e.__class__.__name__}") from e
    if r.status_code >= 400:
        raise HTTPException(404 if r.status_code == 404 else 502, "Datei in IDA nicht gefunden")
    return r.content


@router.get("/ida/file")
async def ida_file(request: Request, src: str, mime: str = "", name: str = ""):
    if not src.startswith(PREFIX):
        raise HTTPException(400, "Ungültige Quelle")
    data = await file_bytes(src)
    ctype = mime if re.match(r"^[\w.+-]+/[\w.+-]+$", mime or "") else (mimetypes.guess_type(name)[0] if name else None) or "application/octet-stream"
    return bytes_response(request, data, ctype, re.sub(r"[\x00-\x1f\\/]", "_", name)[:200])


@router.post(f"/chats/{IDA}/upload")
async def ida_upload(file: UploadFile = File(...), caption: str = Form(""), reply_to: str = Form(""),
                     voice: str = Form(""), duration: str = Form(""), mime: str = Form("")):
    if not enabled():
        raise HTTPException(404, "Ida ist hier nicht verbunden")
    data = await file.read(25 * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(400, "Leere Datei")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Datei zu groß (max. 25 MB)")
    fname = (file.filename or "datei")[:200]
    ctype = (mime or file.content_type or mimetypes.guess_type(fname)[0] or "application/octet-stream").split(";")[0].strip()
    try:
        r = await client().post("/api/v1/uploads", params={"any": "true"}, files={"file": (fname, data, ctype)}, timeout=60)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ida nicht erreichbar: {e.__class__.__name__}") from e
    if r.status_code >= 400:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, f"Ida-Upload: {r.text[:200]}")
    info = r.json()
    text = caption.strip() or f"Datei: {fname}"
    await start(text, files=[info["id"]])
    return {"ok": True, "file": info}


# ---------------- Senden: an den IDA-Chat; die Antwort läuft in IDA weiter, auch wenn die App zu ist ----------------
class IdaSend(BaseModel):
    text: str = Field(max_length=20_000)
    reply_to: str | None = Field(default=None, max_length=300)
    mentions: list = Field(default_factory=list)


async def start(text: str, files: list[str] | None = None, with_hint: bool = True) -> None:
    """Schickt den Text an Ida und kehrt zurück, sobald IDA die Nachricht angenommen hat; die Antwort verfolgt follow()."""
    cid = chat_id()
    full_text = text + (CHAT_HINT if with_hint and CHAT_HINT not in text else "")
    body: dict = {"chat_id": cid, "text": full_text} if cid else {"agent_id": (await agent())["id"], "text": full_text}
    if files:
        body["files"] = files
    req = client().build_request("POST", "/api/v1/chat", json=body, timeout=httpx.Timeout(20, read=None))
    try:
        r = await client().send(req, stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Ida nicht erreichbar: {e.__class__.__name__}") from e
    if r.status_code >= 400:
        detail = (await r.aread()).decode(errors="replace")[:200]
        await r.aclose()
        if r.status_code == 404 and cid:   # Chat in IDA gelöscht: einmal mit neuem Chat
            push._kv_set("ida_chat", "")
            return await start(text, files=files, with_hint=with_hint)
        raise HTTPException(502, f"Ida: {detail}")
    lines = r.aiter_lines()
    try:
        first = json.loads(await lines.__anext__())
    except (StopAsyncIteration, ValueError, httpx.HTTPError) as e:
        await r.aclose()
        raise HTTPException(502, "Ida: unerwartete Antwort") from e
    if (new := (first.get("chat") or {}).get("id")) and new != cid:
        push._kv_set("ida_chat", str(new))
    _running[IDA] = time.time()
    bus.publish({"type": "chat", "id": IDA, "title": "Ida", "new": False})
    asyncio.create_task(follow(r, lines))


async def follow(r: httpx.Response, lines) -> None:
    ok, err = True, ""
    try:
        async for ln in lines:
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if "done" in ev:
                ok, err = bool(ev["done"].get("ok")), str(ev["done"].get("error") or "")
    except httpx.HTTPError as e:
        log.info("Ida-Antwort abgerissen: %s", e)
    finally:
        await r.aclose()
        _running.pop(IDA, None)
    bus.publish({"type": "chat", "id": IDA, "title": "Ida", "new": True})
    if not bus.count():   # App nirgends offen: Antwort als Meldung
        await notify_answer(ok, err)


async def notify_answer(ok: bool, err: str) -> None:
    if not push.has_subs() or push.muted(IDA):
        return
    try:
        msgs, _ = await history()
        last = next((m for m in reversed(msgs) if not m["mine"]), None)
        body = re.sub(r"\s+", " ", last["text"])[:300] if ok and last else f"Antwort ging nicht: {err[:200]}" if not ok else "Neue Antwort"
        name = (await agent()).get("name") or "Ida"
        push.new_note(IDA)
        await push.send({"chat": IDA, "from": IDA, "title": name, "body": body, "unread": 1, "at": last["at"] if last else "", "av": IDA})
    except Exception as e:  # noqa: BLE001
        log.warning("Push für Ida: %s", e)


@router.post(f"/chats/{IDA}/send")
async def ida_send(body: IdaSend):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Leere Nachricht")
    await start(text)
    return {"ok": True}


# ---------------- „An Ida schicken“: Nachricht mit Kontext aus einem anderen Chat ----------------
class Line(BaseModel):
    who: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=4000)
    at: str = Field(default="", max_length=40)
    mine: bool = False


class Forward(BaseModel):
    chat_id: str = Field(max_length=300)
    title: str = Field(default="", max_length=200)
    network: str = Field(default="", max_length=60)
    group: bool = False
    message: Line
    before: list[Line] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=2000)


def _line(x: Line) -> str:
    who = "Simon" if x.mine else (x.who or "?")
    t = (x.at or "")[:16].replace("T", " ")
    return f"[{t}] {who}: {x.text.strip() or '(Anhang ohne Text)'}"


def forward_text(f: Forward) -> str:
    net = f" über {f.network}" if f.network else ""
    out = [f"↪ Aus dem Chat „{f.title or f.chat_id}“{net}{' (Gruppe)' if f.group else ''}:", "", _line(f.message)]
    if f.note.strip():
        out += ["", f.note.strip()]
    out += [
        "",
        "— Kontext für dich —",
        f"Chat-ID: {f.chat_id}",
        "Wichtig: Schau bei unbekannten Personen, Gruppen, Lehrkräften oder Themen zuerst in deinem Gedächtnis (memory_search) und im Wiki (wiki_search, wiki_read) nach und speichere wichtige neue Infos dort (memory_save, wiki_memory_save). "
        "Geht es um einen Termin oder eine Frist, prüfe direkt den Kalender (calendar_events / calendar_conflicts) und trage ihn ein oder frag kurz nach (mit [OPTIONEN: Ja, eintragen | Nein] am Ende).",
    ]
    if f.before:
        out += ["Nachrichten davor:", *(_line(x) for x in f.before)]
    return "\n".join(out)


@router.post("/ida/forward")
async def ida_forward(body: Forward):
    await start(forward_text(body))
    return {"ok": True}


# ---------------- Meldungen aus dem IDA-Postfach ans Handy (und live in die offene App) ----------------
async def loop() -> None:
    """Alle 20 s (App offen) bzw. 60 s: neue Benachrichtigungen an dich? Dann live Bescheid geben und (App zu) am Handy melden."""
    if not enabled():
        return
    last = push._kv("ida_notice")
    while True:
        wait = 20 if bus.count() else 60
        try:
            items, _ = await notices(10)
            newest = max(( _z(x.get("at")) for x in items ), default="")
            if newest and newest > last:
                if last:   # nicht beim allerersten Start alte Meldungen noch mal schicken
                    bus.publish({"type": "chat", "id": IDA, "title": "Ida", "new": True})
                    if not bus.count() and push.has_subs() and not push.muted(IDA):
                        for n in reversed([x for x in items if _z(x.get("at")) > last]):
                            push.new_note(IDA)
                            await push.send({"chat": IDA, "from": IDA, "title": (n.get("title") or n.get("from_name") or "Ida")[:120],
                                             "body": plain(n.get("text") or "")[:300], "unread": 1, "at": _z(n.get("at")), "av": IDA})
                last = newest
                push._kv_set("ida_notice", last)
        except Exception as e:  # noqa: BLE001
            log.debug("Ida-Postfach: %s", e)
        await asyncio.sleep(wait)
