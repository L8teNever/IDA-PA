"""WebUntis-Nachrichten als Chats: pro Lehrkraft ein Chat (id "untis:<schlüssel>"), darin die Nachrichten von ihr und deine an sie.
Schreiben im Chat antwortet auf ihre letzte Nachricht (oder auf die, auf die du mit „Antworten“ zeigst); gibt es keine, geht eine neue
Nachricht an sie. Anmeldung in den Einstellungen (Schule, Benutzername, Passwort – nur hier in chats.db, nie im Repo).

Schnittstellen (die gleichen, die die WebUntis-Webseite selbst nutzt – aus ihrem Code abgelesen, nicht offiziell dokumentiert):
  Anmeldung   POST /WebUntis/jsonrpc.do?school=…  authenticate → JSESSIONID; GET /WebUntis/api/token/new → JWT (Bearer, ~15 min)
  Postfach    GET  api/rest/view/v1/messages                {incomingMessages, readConfirmationMessages}
  Gesendet    GET  api/rest/view/v1/messages/sent           {sentMessages}
  Nachricht   GET  api/rest/view/v1/messages/{id}           ganzer Text, Empfänger, replyHistory – markiert sie als gelesen
  Lehrkräfte  GET  api/rest/view/v1/messages/recipients/static/teachers
  Neu         POST api/rest/view/v2/messages                multipart, Teil "request": {subject, content, recipientOption, recipientPersonIds, …}
  Antworten   POST api/rest/view/v2/messages/{id}/reply     multipart, Teil "request": {subject, content}
              (Dateien beim Senden/Antworten: weitere Teile "attachments")
  Anhänge     in der ganzen Nachricht (und replyHistory): storageAttachments [{id, name}] →
              GET api/rest/view/v1/messages/{id}/attachmentstorageurl → {downloadUrl, additionalHeaders} (S3, Header mitschicken);
              blobAttachment {name} → GET api/rest/view/v1/messages/{nachricht}/attachment; attachments = OneDrive-Links
Siehe docs/CHATS.md."""
import asyncio
import base64
import html
import json
import logging
import mimetypes
import re
import time
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from . import bus, push
from .api import bytes_response, plain

log = logging.getLogger("chats.untis")
router = APIRouter(prefix="/api/v1", tags=["untis"])
PREFIX = "untis:"
UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.webuntis\.com$")


# ---------------- Anmeldedaten (chats.db) ----------------
def creds() -> dict | None:
    try:
        c = json.loads(push._kv("untis_login") or "null")
    except ValueError:
        return None
    return c if isinstance(c, dict) and c.get("server") and c.get("user") else None


def enabled() -> bool:
    return creds() is not None


# ---------------- Sitzung ----------------
class Sess:
    def __init__(self, c: httpx.AsyncClient, base: str, jwt: str, tenant: str, until: float):
        self.c, self.base, self.jwt, self.tenant, self.until = c, base, jwt, tenant, until


_sess: Sess | None = None
_lock = asyncio.Lock()


def _claims(jwt: str) -> dict:
    try:
        p = jwt.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    except (IndexError, ValueError):
        return {}


def _token(raw: str) -> str:
    t = raw.strip()
    if not t or t.startswith("<"):
        return ""
    if t.startswith("{"):
        try:
            d = json.loads(t)
            return next((str(d[k]) for k in ("token", "accessToken", "access_token", "jwt") if d.get(k)), "")
        except ValueError:
            return ""
    return t.strip('"')


def base_url(server: str) -> str:
    return f"https://{server}/WebUntis"


async def login(cr: dict) -> Sess:
    """Anmelden wie die Untis-Apps (JSON-RPC authenticate), dann den JWT für die REST-Schnittstelle holen."""
    server, school = cr["server"], cr["school"]
    if not HOST.match(server):
        raise HTTPException(400, "Ungültiger Untis-Server")
    base = base_url(server)
    c = httpx.AsyncClient(timeout=20, headers={"user-agent": UA}, follow_redirects=True)
    c.cookies.set("schoolname", "_" + base64.b64encode(school.encode()).decode())
    try:
        r = await c.post(f"{base}/jsonrpc.do", params={"school": school},
                         json={"id": "chats", "method": "authenticate", "jsonrpc": "2.0",
                               "params": {"user": cr["user"], "password": cr["password"], "client": "IDA-Chats"}})
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except (httpx.HTTPError, ValueError) as e:
        await c.aclose()
        raise HTTPException(502, f"WebUntis nicht erreichbar: {e.__class__.__name__}") from e
    if not (d.get("result") or {}).get("sessionId"):
        await c.aclose()
        msg = (d.get("error") or {}).get("message") or f"HTTP {r.status_code}"
        raise HTTPException(401, f"WebUntis-Anmeldung abgelehnt: {msg}")
    c.cookies.set("JSESSIONID", d["result"]["sessionId"])
    jwt = await _new_jwt(c, base)
    if not jwt:
        await c.aclose()
        raise HTTPException(401, "WebUntis: angemeldet, aber kein Zugang zu den Nachrichten bekommen")
    cl = _claims(jwt)
    until = float(cl.get("exp") or time.time() + 600) - 60
    return Sess(c, base, jwt, str(cl.get("tenant_id") or cl.get("tenantId") or ""), until)


async def _new_jwt(c: httpx.AsyncClient, base: str) -> str:
    try:
        r = await c.get(f"{base}/api/token/new", headers={"accept": "text/plain, application/json"})
        return _token(r.text) if r.status_code == 200 else ""
    except httpx.HTTPError:
        return ""


async def session(fresh: bool = False) -> Sess:
    global _sess
    async with _lock:
        cr = creds()
        if not cr:
            raise HTTPException(404, "WebUntis ist nicht verbunden (Einstellungen → WebUntis)")
        if _sess and not fresh and time.time() < _sess.until:
            return _sess
        if _sess and not fresh:   # JWT abgelaufen: mit der Sitzung einen neuen holen, sonst neu anmelden
            jwt = await _new_jwt(_sess.c, _sess.base)
            if jwt:
                _sess.jwt, _sess.until = jwt, float(_claims(jwt).get("exp") or time.time() + 600) - 60
                return _sess
        if _sess:
            await _sess.c.aclose()
            _sess = None
        _sess = await login(cr)
        return _sess


async def rest(method: str, path: str, *, retry: bool = True, **kw) -> dict | list:
    s = await session()
    h = {"authorization": f"Bearer {s.jwt}", "accept": "application/json", **({"tenant-id": s.tenant} if s.tenant else {})}
    try:
        r = await s.c.request(method, f"{s.base}/api/rest/view/{path}", headers=h, **kw)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"WebUntis nicht erreichbar: {e.__class__.__name__}") from e
    if r.status_code in (401, 403) and retry:
        await session(fresh=True)
        return await rest(method, path, retry=False, **kw)
    if r.status_code >= 400:
        try:
            d = r.json()
            msg = d.get("errorMessage") or "; ".join(x.get("errorMessage", "") for x in d.get("validationErrors") or [])
        except (ValueError, AttributeError):
            msg = ""
        raise HTTPException(502, f"WebUntis: {msg or f'HTTP {r.status_code}'}")
    if not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {}


async def rest_raw(path: str, retry: bool = True) -> bytes:
    """Wie rest(), aber die rohen Bytes (Dateien)."""
    s = await session()
    h = {"authorization": f"Bearer {s.jwt}", **({"tenant-id": s.tenant} if s.tenant else {})}
    try:
        r = await s.c.get(f"{s.base}/api/rest/view/{path}", headers=h, timeout=120)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"WebUntis nicht erreichbar: {e.__class__.__name__}") from e
    if r.status_code in (401, 403) and retry:
        await session(fresh=True)
        return await rest_raw(path, retry=False)
    if r.status_code >= 400:
        raise HTTPException(404 if r.status_code == 404 else 502, f"WebUntis: Datei nicht abrufbar (HTTP {r.status_code})")
    return r.content


# ---------------- Daten: Postfach, Gesendet, einzelne Nachrichten ----------------
_cache: dict[str, tuple[float, object]] = {}
_detail: dict[int, dict] = {}     # Nachricht → Inhalt (ändert sich nicht; nur gesehene / gesendete)


async def cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    v = await fn()
    _cache[key] = (time.monotonic(), v)
    return v


def drop_cache() -> None:
    _cache.clear()


async def inbox() -> list[dict]:
    async def get():
        d = await rest("GET", "v1/messages")
        seen, out = set(), []
        for m in (d.get("incomingMessages") or []) + (d.get("readConfirmationMessages") or []):
            if isinstance(m, dict) and m.get("id") not in seen:
                seen.add(m.get("id"))
                out.append(m)
        return out
    return await cached("inbox", 20, get)


async def sent() -> list[dict]:
    async def get():
        d = await rest("GET", "v1/messages/sent")
        return [m for m in d.get("sentMessages") or [] if isinstance(m, dict)][:40]
    return await cached("sent", 60, get)


async def detail(mid: int) -> dict:
    """Ganze Nachricht (markiert eine empfangene als gelesen)."""
    if mid not in _detail:
        d = await rest("GET", f"v1/messages/{mid}")
        if isinstance(d, dict):
            _detail[mid] = d
    return _detail.get(mid) or {}


async def sent_details() -> list[dict]:
    """Gesendete mit Empfängern (die Liste allein nennt sie nicht); Abruf hat keine Nebenwirkung."""
    items = await sent()
    sem = asyncio.Semaphore(4)

    async def one(m):
        async with sem:
            try:
                return {**m, **await detail(int(m["id"]))}
            except (HTTPException, KeyError, ValueError):
                return None
    return [x for x in await asyncio.gather(*(one(m) for m in items)) if x]


# ---------------- Lehrkräfte ----------------
def key_of(name: str) -> str:
    """Stabiler Schlüssel aus dem Namen (WebUntis nennt Absender und Empfänger mit unterschiedlichen IDs, der Name passt immer)."""
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", n).strip("-")[:80] or "unbekannt"


def _people(x, out: list, group_type: str = "") -> None:
    """Alle Personen aus einer Antwort, egal wie verschachtelt (Gruppen mit persons/recipients)."""
    if isinstance(x, list):
        for y in x:
            _people(y, out, group_type)
    elif isinstance(x, dict):
        gt = x.get("type") if isinstance(x.get("teachers"), list) else group_type
        if x.get("displayName") and any(x.get(k) for k in ("personId", "id", "userId")) and x.get("type") != "GROUP":
            out.append({**x, "_gt": gt})
        for k in ("teachers", "persons", "recipients", "groupRecipients", "data", "items", "users"):
            if k in x:
                _people(x[k], out, gt)


async def subjects() -> dict[str, str]:
    """Kürzel → voller Fachname aus WebUntis (z. B. AT → Angewandte Technik)."""
    async def get():
        s = await session()
        cr = creds() or {}
        try:
            r = await s.c.post(f"{s.base}/jsonrpc.do", params={"school": cr.get("school", "")},
                               json={"id": "sub", "method": "getSubjects", "params": {}, "jsonrpc": "2.0"})
            res = (r.json() if r.status_code == 200 else {}).get("result") or []
        except (httpx.HTTPError, ValueError):
            res = []
        return {str(x["name"]).strip(): str(x.get("longName") or "").strip() for x in res if isinstance(x, dict) and x.get("name")}
    return await cached("subjects", 86400, get)


def _format_tags(tags: list[str], smap: dict[str, str]) -> list[str]:
    out = []
    for raw in tags:
        t = str(raw or "").strip()
        if not t:
            continue
        if t == "KL":
            out.append("KL (Klassenlehrkraft)")
            continue
        base, _, rest = t.partition(" ")
        long = smap.get(t) or smap.get(base)
        if long and long.lower() != base.lower():
            out.append(f"{t} ({long})" if not rest else f"{base} ({long}) {rest}")
        else:
            out.append(t)
    return out


async def teachers() -> list[dict]:
    async def get():
        raw: list = []
        for path in ("v1/messages/recipients/static/teachers", "v1/messages/recipients/static/persons"):
            try:
                _people(await rest("GET", path), raw)
            except HTTPException:
                continue
            if raw:
                break
        smap = await subjects()
        out: dict[str, dict] = {}
        for p in raw:
            k = key_of(p["displayName"])
            tags = [str(x).strip() for x in p.get("tags") or [] if str(x).strip()]
            e = out.setdefault(k, {"key": k, "name": p["displayName"].strip(), "pid": p.get("personId") or p.get("id") or p.get("userId"),
                                   "sub": p.get("subtitle") or p.get("className") or "", "tags": [], "subjects": [], "class_teacher": False})
            for tg in tags:
                if tg not in e["tags"]:
                    e["tags"].append(tg)
            if p.get("_gt") == "CLASS_TEACHERS" or "KL" in tags:
                e["class_teacher"] = True
        for e in out.values():
            e["subjects"] = _format_tags(e["tags"], smap)
            if not e["sub"] and e["tags"]:
                e["sub"] = ", ".join(e["tags"])
        return sorted(out.values(), key=lambda t: t["name"].lower())
    return await cached("teachers", 3600, get)


def _sender(m: dict) -> str:
    return ((m.get("sender") or {}).get("displayName") or "").strip()


def _recips(m: dict) -> list[str]:
    out: list = []
    _people([m.get("recipientPersons") or [], m.get("recipients") or []], out)
    return [p["displayName"].strip() for p in out]


BERLIN = ZoneInfo("Europe/Berlin")


def _when(m: dict) -> str:
    """WebUntis liefert deutsche Ortszeit ohne Zone → UTC mit Z (wie Beeper), damit Sortierung und Anzeige stimmen."""
    s = str(m.get("sentDateTime") or "")
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    if d.tzinfo is None:
        d = d.replace(tzinfo=BERLIN)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _content(m: dict) -> str:
    c = m.get("content")
    if c is None:
        c = m.get("contentPreview") or ""
    t = plain(c) if "<" in c else c
    return html.unescape(t).strip()


STORAGE_ID = re.compile(r"^[A-Za-z0-9-]{8,80}$")


def _kind(mime: str) -> str:
    return next((k for k in ("image", "video", "audio") if mime.startswith(k + "/")), "file")


def _file(src: str, name: str) -> dict:
    name = (name or "Datei").strip()[:200]
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return {"src": src, "kind": _kind(mime), "mime": mime, "name": name, "size": 0, "duration": 0, "w": None, "h": None,
            "transcript": "", "sticker": False, "gif": mime == "image/gif", "poster": ""}


def _files(m: dict) -> tuple[list[dict], list[dict]]:
    """Anhänge einer ganzen Nachricht → (Dateien zum Anzeigen, OneDrive-Links)."""
    files = [_file(f"untis:s:{a['id']}", a.get("name") or "") for a in m.get("storageAttachments") or []
             if isinstance(a, dict) and STORAGE_ID.match(str(a.get("id") or ""))]
    b = m.get("blobAttachment")
    if isinstance(b, dict) and m.get("id"):
        files.append(_file(f"untis:b:{int(m['id'])}", b.get("name") or ""))
    links = []
    for a in m.get("attachments") or []:
        u = isinstance(a, dict) and str(a.get("url") or a.get("downloadUrl") or a.get("webUrl") or "")
        if u and u.lower().startswith("https://"):
            links.append({"url": u, "title": (a.get("name") or "OneDrive-Datei")[:200], "summary": "OneDrive", "img": "", "w": None, "h": None})
    return files, links[:3]


def _text(m: dict, with_subject: bool = True, known: bool = False) -> str:
    sub, body = (m.get("subject") or "").strip(), _content(m)
    if with_subject and sub and not re.match(r"^(aw|re|antwort)\s*:", sub, re.I) and sub not in body[:len(sub) + 5]:
        body = f"{sub}\n\n{body}" if body else sub
    if not known and m.get("hasAttachments"):   # nur die Liste, nicht die ganze Nachricht: Anhang bekannt, aber nicht welcher
        body += "\n📎 Anhang (in WebUntis öffnen)"
    return body.strip()


def msg(m: dict, mine: bool, name: str, **extra) -> dict:
    files, links = _files(m)
    known = bool(files or links) or any(k in m for k in ("storageAttachments", "blobAttachment", "attachments"))
    return {"id": f"u{m['id']}", "at": _when(m), "mine": mine, "sender": "" if mine else name, "sender_id": "", "type": "MEDIA" if files else "TEXT",
            "text": _text(m, known=known), "deleted": bool(m.get("isRevoked")), "edited": False, "reply_to": None, "reactions": [], "mentions": [],
            "at_me": False, "files": files, "links": links, "read_by": [], **extra}


# ---------------- Chats (Liste) ----------------
async def rows() -> list[dict]:
    """Ein Chat pro Lehrkraft mit Nachrichten (von ihr oder an sie)."""
    by: dict[str, dict] = {}

    def add(name: str, m: dict, mine: bool, unread: bool):
        k = key_of(name)
        r = by.setdefault(k, {"id": PREFIX + k, "title": name, "network": "untis", "type": "single", "untis": True, "unread": 0, "at": "", "last": None, "muted": False})
        r["unread"] += 1 if unread else 0
        at = _when(m)
        if at >= r["at"]:
            r["at"] = at
            r["last"] = {"text": (("📎 " if m.get("hasAttachments") else "") + re.sub(r"\s+", " ", (m.get("subject") or "") + " – " + (m.get("contentPreview") or _content(m))))[:140].strip(" –"),
                         "mine": mine, "sender": "", "at": at}
    for m in await inbox():
        if _sender(m):
            add(_sender(m), m, False, not m.get("isMessageRead"))
    for m in await sent_details():
        for n in _recips(m)[:3]:
            add(n, m, True, False)
    return sorted(by.values(), key=lambda r: r["at"], reverse=True)


async def teacher(key: str) -> dict | None:
    return next((t for t in await teachers() if t["key"] == key), None)


async def thread(key: str) -> tuple[list[dict], str, list[dict]]:
    """(Nachrichten alt → neu, Name der Lehrkraft, ihre empfangenen Nachrichten mit Inhalt)"""
    name, items, got = "", {}, []
    for m in await inbox():
        if key_of(_sender(m)) != key:
            continue
        name = _sender(m)
        try:
            d = {**m, **await detail(int(m["id"]))}   # liest den ganzen Text (und markiert sie in WebUntis als gelesen)
        except HTTPException:
            d = m
        got.append(d)
        items[d["id"]] = msg(d, False, name, subject=d.get("subject") or "", can_reply=bool(d.get("isReplyAllowed", True)))
        for h in d.get("replyHistory") or []:   # der Verlauf dieser Unterhaltung (auch deine Antworten)
            if isinstance(h, dict) and h.get("id") and h["id"] not in items:
                mine = key_of(_sender(h)) != key
                items[h["id"]] = msg(h, mine, name)
    for m in await sent_details():
        if any(key_of(n) == key for n in _recips(m)):
            name = name or next(n for n in _recips(m) if key_of(n) == key)
            items.setdefault(m["id"], msg(m, True, name))
    if not name:
        t = await teacher(key)
        name = t["name"] if t else key
    for m in items.values():
        m["sender"] = "" if m["mine"] else name
    return sorted(items.values(), key=lambda x: x["at"]), name, got


def _cid(chat_id: str) -> str:
    if not chat_id.startswith(PREFIX) or not re.match(r"^[a-z0-9-]{1,80}$", chat_id[len(PREFIX):]):
        raise HTTPException(400, "Ungültige ID")
    return chat_id[len(PREFIX):]


# ---------------- Schnittstelle für die Seite ----------------
class Login(BaseModel):
    server: str = Field(max_length=120)
    school: str = Field(max_length=120)
    school_name: str = Field(default="", max_length=200)
    user: str = Field(max_length=120)
    password: str = Field(max_length=200)


@router.get("/untis")
async def status():
    c = creds()
    if not c:
        return {"on": False}
    return {"on": True, "user": c["user"], "school": c.get("school_name") or c["school"], "school_login": c["school"], "server": c["server"]}


@router.get("/untis/schools")
async def schools(q: str = ""):
    """Schulsuche wie in den Untis-Apps (öffentliches Verzeichnis)."""
    q = q.strip()[:80]
    if len(q) < 3:
        return {"items": []}
    try:
        async with httpx.AsyncClient(timeout=10, headers={"user-agent": UA}) as c:
            r = await c.post("https://mobile.webuntis.com/ms/schoolquery2",
                             json={"id": "1", "method": "searchSchool", "params": [{"search": q}], "jsonrpc": "2.0"})
            d = r.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Untis-Schulsuche nicht erreichbar")
    if d.get("error"):   # zu viele Treffer
        return {"items": [], "hint": "Zu viele Treffer – genauer suchen (z. B. Ort dazu)"}
    out = []
    for s in (d.get("result") or {}).get("schools") or []:
        server = str(s.get("server") or "").split("/")[0]
        if HOST.match(server):
            out.append({"name": s.get("displayName") or s.get("loginName"), "school": s.get("loginName"), "server": server, "address": s.get("address") or ""})
    return {"items": out[:30]}


@router.put("/untis")
async def connect(body: Login):
    global _sess
    cr = {"server": body.server.strip().lower().removeprefix("https://").split("/")[0], "school": body.school.strip(),
          "school_name": body.school_name.strip(), "user": body.user.strip(), "password": body.password}
    s = await login(cr)   # Fehler gehen als Meldung an die Seite
    async with _lock:
        if _sess:
            await _sess.c.aclose()
        _sess = s
    push._kv_set("untis_login", json.dumps(cr))
    push._kv_set("untis_seen", "")
    drop_cache()
    _detail.clear()
    bus.publish({"type": "list"})
    return await status()


@router.delete("/untis")
async def disconnect():
    global _sess
    push._kv_set("untis_login", "")
    async with _lock:
        if _sess:
            await _sess.c.aclose()
        _sess = None
    drop_cache()
    _detail.clear()
    bus.publish({"type": "list"})
    return {"on": False}


@router.get("/untis/chats")
async def chats():
    if not enabled():
        return {"on": False, "items": []}
    try:
        return {"on": True, "items": await rows()}
    except HTTPException as e:
        return {"on": True, "items": [], "error": e.detail}


@router.get("/untis/teachers")
async def teacher_list():
    """Alle Lehrkräfte, denen du schreiben darfst – für die Suche (neuer Chat)."""
    if not enabled():
        return {"items": []}
    try:
        return {"items": [{"id": PREFIX + t["key"], "title": t["name"], "network": "untis", "type": "single", "untis": True, "unread": 0, "at": ""}
                          for t in await teachers()]}
    except HTTPException:
        return {"items": []}


@router.get("/chats/" + PREFIX + "{key}/messages")
async def messages(key: str, cursor: str = "", direction: str = "before"):
    _cid(PREFIX + key)
    if cursor:
        return {"items": [], "more": False, "oldest": None, "newest": None}
    items, _, got = await thread(key)
    if any(not g.get("isMessageRead") for g in got):   # gerade gelesen: Liste und Zähler frisch
        _cache.pop("inbox", None)
        bus.publish({"type": "chat", "id": PREFIX + key, "title": "", "new": False, "unread": 0})
    return {"items": items, "more": False, "oldest": None, "newest": None}


@router.post("/chats/" + PREFIX + "{key}/read")
async def read(key: str):
    _cid(PREFIX + key)
    for m in await inbox():
        if key_of(_sender(m)) == key and not m.get("isMessageRead"):
            try:
                await detail(int(m["id"]))   # Öffnen der Nachricht = gelesen
            except HTTPException:
                pass
    _cache.pop("inbox", None)
    asyncio.create_task(push.dismiss(PREFIX + key))
    return {"ok": True}


async def about_one(key: str) -> dict:
    _cid(PREFIX + key)
    t = await teacher(key) if enabled() else None
    if not t:
        _, name, _ = await thread(key) if enabled() else ([], key, [])
    else:
        name = t["name"]
    c = creds() or {}
    subs = (t or {}).get("subjects") or []
    return {
        "id": PREFIX + key, "title": name or key, "network": "untis", "group": False, "untis": True,
        "subjects": subs, "class_teacher": bool((t or {}).get("class_teacher")),
        "school": c.get("school_name") or c.get("school") or "",
        "web_url": f"https://{c['server']}/WebUntis/?school={c['school']}#/basic/messages" if c.get("server") and c.get("school") else "",
        "people": [{"id": str((t or {}).get("pid") or key), "name": name or key, "user": ", ".join(subs), "phone": "", "email": "", "admin": False}],
    }


@router.get("/chats/" + PREFIX + "{key}/about")
async def about(key: str):
    return {"chats": [await about_one(key)]}


@router.get("/avatar/" + PREFIX + "{key}")
async def avatar(key: str):
    return Response(status_code=404, headers={"cache-control": "private, max-age=86400"})


_blobs: dict[str, bytes] = {}   # zuletzt geholte Anhänge (Bilder werden oft mehrmals geladen)
BLOB_CACHE = 60 * 1024 * 1024


async def file_bytes(src: str) -> bytes:
    """Bytes eines Untis-Anhangs: untis:s:<speicher-id> oder untis:b:<nachricht>."""
    if src in _blobs:
        return _blobs[src]
    kind, _, ref = src[len(PREFIX):].partition(":")
    if kind == "s" and STORAGE_ID.match(ref):
        d = await rest("GET", f"v1/messages/{ref}/attachmentstorageurl")
        url = isinstance(d, dict) and str(d.get("downloadUrl") or "")
        if not url or not url.lower().startswith(("https://", "http://")):
            raise HTTPException(404, "WebUntis: Anhang nicht gefunden")
        h = {x["key"]: x["value"] for x in d.get("additionalHeaders") or [] if isinstance(x, dict) and x.get("key") and x.get("value") is not None}
        h.pop("host", None)
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
                r = await c.get(url, headers=h)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Anhang nicht erreichbar: {e.__class__.__name__}") from e
        if r.status_code >= 400:
            raise HTTPException(502, f"Anhang nicht abrufbar (HTTP {r.status_code})")
        data = r.content
    elif kind == "b" and ref.isdigit():
        data = await rest_raw(f"v1/messages/{int(ref)}/attachment")
    else:
        raise HTTPException(400, "Ungültiger Anhang")
    while _blobs and sum(len(v) for v in _blobs.values()) + len(data) > BLOB_CACHE:
        _blobs.pop(next(iter(_blobs)))
    if len(data) <= BLOB_CACHE // 3:
        _blobs[src] = data
    return data


@router.get("/untis/file")
async def file(request: Request, src: str, mime: str = "", name: str = ""):
    """Anhang einer Untis-Nachricht (Bild, PDF, …) – über den Dienst, die App hat keinen WebUntis-Zugang."""
    if not src.startswith(PREFIX) or len(src) > 120:
        raise HTTPException(400, "Ungültiger Anhang")
    data = await file_bytes(src)
    ctype = mime if re.match(r"^[\w.+-]+/[\w.+-]+$", mime or "") else (mimetypes.guess_type(name)[0] if name else None) or "application/octet-stream"
    return bytes_response(request, data, ctype, re.sub(r"[\x00-\x1f\\/]", "_", name)[:200])


class Send(BaseModel):
    text: str = Field(max_length=20_000)
    reply_to: str | None = Field(default=None, max_length=40)
    mentions: list = Field(default_factory=list)


def _html(text: str) -> str:
    return "<p>" + "<br>".join(html.escape(line) for line in text.split("\n")) + "</p>"


def _form(req: dict, files: list[tuple[str, bytes, str]] = ()) -> dict:
    parts = [("request", (None, json.dumps(req, ensure_ascii=False).encode(), "application/json"))]
    parts += [("attachments", f) for f in files]
    return {"files": parts}


@router.post("/chats/" + PREFIX + "{key}/send")
async def send(key: str, body: Send):
    _cid(PREFIX + key)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Leere Nachricht")
    return await deliver(key, text, body.reply_to)


UPLOAD_MAX = 50 * 1024 * 1024


@router.post("/chats/" + PREFIX + "{key}/upload")
async def upload(key: str, file: list[UploadFile] = File(...), text: str = Form(""), reply_to: str = Form(""),
                 voice: bool = Form(False), duration: float = Form(0)):
    """Datei(en) an die Lehrkraft – als Anhang einer Antwort oder neuen Nachricht (WebUntis nimmt jede Art von Datei)."""
    _cid(PREFIX + key)
    files, total = [], 0
    for f in file[:10]:
        data = await f.read(UPLOAD_MAX + 1)
        total += len(data)
        if total > UPLOAD_MAX:
            raise HTTPException(413, "Dateien zu groß (zusammen höchstens 50 MB)")
        if data:
            name = re.sub(r"[\\/\x00-\x1f]", "_", f.filename or "datei")[:200] or "datei"
            files.append((name, data, (f.content_type or "application/octet-stream").split(";")[0].strip().lower()))
    if not files:
        raise HTTPException(400, "Leere Datei")
    return await deliver(key, text.strip()[:20_000], reply_to or None, files)


async def deliver(key: str, text: str, reply_to: str | None, files: list[tuple[str, bytes, str]] = ()) -> dict:
    """Antwort auf ihre (letzte oder genannte) Nachricht oder – gibt es keine – neue Nachricht; optional mit Dateien."""
    items, name, got = await thread(key)
    target = None
    if reply_to and re.match(r"^u\d+$", reply_to):   # „Antworten“ auf eine bestimmte Nachricht
        rid = int(reply_to[1:])
        target = next((g for g in got if g["id"] == rid), None) or {"id": rid, "subject": next((m.get("subject") for m in items if m["id"] == reply_to), "")}
    elif got:   # sonst auf ihre letzte Nachricht, wenn sie Antworten erlaubt
        last = max(got, key=_when)
        if last.get("isReplyAllowed", True):
            target = last
    if target:
        sub = (target.get("subject") or "").strip()
        try:
            form = await rest("GET", f"v1/messages/{target['id']}/reply-form")
            sub = (form.get("subject") if isinstance(form, dict) else "") or sub
        except HTTPException:
            pass
        if sub and not re.match(r"^(aw|re)\s*:", sub, re.I):
            sub = f"AW: {sub}"
        await rest("POST", f"v2/messages/{target['id']}/reply", timeout=300, **_form({"subject": sub or "AW:", "content": _html(text) if text else ""}, files))
        how = "reply"
    else:   # noch nichts von ihr: neue Nachricht, Betreff = Anfang des Textes
        t = await teacher(key)
        if not t or not t.get("pid"):
            raise HTTPException(400, f"{name}: in WebUntis nicht als Empfänger erlaubt")
        first = text.split("\n", 1)[0].strip() or (files[0][0] if files else "")
        sub = first if len(first) <= 80 else first[:77].rstrip() + "…"
        await rest("POST", "v2/messages", timeout=300, **_form({"subject": sub, "content": _html(text) if text else "", "recipientOption": "TEACHER",
                                                     "recipientPersonIds": [t["pid"]], "recipientGroupIds": [], "requestConfirmation": False,
                                                     "copyToStudent": False, "forbidReply": False}, files))
        how = "new"
    _cache.pop("sent", None)
    bus.publish({"type": "chat", "id": PREFIX + key, "title": name, "new": False})
    return {"ok": True, "how": how, "files": len(files)}


# ---------------- Neue Nachrichten melden ----------------
async def loop() -> None:
    """Alle 2 Minuten (App offen: 30 s) ins Postfach schauen; Neues live an die App und als Meldung ans Handy."""
    while True:
        wait = 30 if bus.count() else 120
        if enabled():
            try:
                _cache.pop("inbox", None)
                items = await inbox()
                try:
                    seen = set(json.loads(push._kv("untis_seen") or "null") or [])
                    first = not push._kv("untis_seen")
                except ValueError:
                    seen, first = set(), True
                fresh = [m for m in items if m.get("id") not in seen and not m.get("isMessageRead")]
                push._kv_set("untis_seen", json.dumps([m.get("id") for m in items][:300]))
                if fresh and not first:
                    for m in fresh:
                        cid, name = PREFIX + key_of(_sender(m)), _sender(m) or "WebUntis"
                        bus.publish({"type": "chat", "id": cid, "title": name, "new": True})
                        if push.has_subs() and not push.muted(cid):
                            push.new_note(cid)
                            body = re.sub(r"\s+", " ", f"{m.get('subject') or ''} – {m.get('contentPreview') or ''}").strip(" –")[:300]
                            await push.send({"chat": cid, "from": cid, "title": f"{name} (Untis)"[:80], "body": body or "Neue Nachricht",
                                             "unread": 1, "at": _when(m), "av": cid})
            except Exception as e:  # noqa: BLE001 – darf nie sterben
                log.info("WebUntis-Postfach: %s", getattr(e, "detail", e))
                wait = 300
        await asyncio.sleep(wait)
