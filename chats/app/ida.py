"""Chat „Ida“: ein eigener Chat in der Liste, in dem du direkt mit Ida (dem Haupt-Agenten von IDA) schreibst. Dahinter steckt ein
normaler IDA-Chat (beim ersten Senden angelegt, ID in chats.db). Dazu kommen Idas Benachrichtigungen an dich (IDA-Postfach: Erinnerungen,
notify_user, Antworten anderer Agenten) – sie stehen im selben Verlauf und melden sich am Handy wie eine Chat-Nachricht.
„An Ida schicken“ (langes Drücken auf eine Nachricht) schickt ihr die Nachricht mit dem Chat und den Nachrichten davor als Kontext.

Die Routen hängen vor denen von api.py: /chats/ida/... landet hier, alle anderen Chats gehen wie bisher an Beeper. Siehe docs/CHATS.md."""
import asyncio
import json
import logging
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from . import bus, push
from .config import get_settings

log = logging.getLogger("chats.ida")
router = APIRouter(prefix="/api/v1", tags=["ida"])
IDA = "ida"   # Chat-ID in der App (Beeper-IDs sehen ganz anders aus: "!…:beeper.local")
_c: httpx.AsyncClient | None = None
_agent: dict = {"t": 0.0, "v": None}
_running: dict[str, float] = {}   # läuft gerade eine Antwort? (seit wann)


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


# ---------------- Text: Idas Markdown wird lesbarer Klartext (die App zeigt keine Formatierung) ----------------
def plain(t: str) -> str:
    t = re.sub(r"```[a-z]*\n?", "", t or "")
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m[1] or m[2], t)
    t = re.sub(r"`([^`\n]+)`", r"\1", t)
    t = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", lambda m: m[2] if m[1] == m[2] else f"{m[1]} ({m[2]})", t)
    t = re.sub(r"^(\s*)[-*]\s+", r"\1• ", t, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


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
    for m in (d.get("messages") or [])[-150:]:
        if m.get("role") not in ("user", "assistant") or not (m.get("text") or "").strip():
            continue
        mine = m["role"] == "user"
        out.append(_msg(f"c{m['id']}", _z(m.get("at")), (m.get("text") or "").strip() if mine else plain(m["text"]), mine, "" if mine else name))
    return out, bool(d.get("running"))


async def notices(limit: int = 60) -> tuple[list[dict], int]:
    """Benachrichtigungen an dich aus dem IDA-Postfach (neueste zuerst) und wie viele davon ungelesen sind."""
    d = await ida("GET", "/api/v1/messages/inbox", params={"limit": limit})
    return [x for x in d.get("items") or [] if isinstance(x, dict)], int(d.get("unread") or 0)


def notice_msg(n: dict) -> dict:
    title, text = (n.get("title") or "").strip(), plain(n.get("text") or "")
    return _msg(f"n{n['id']}", _z(n.get("at")), f"{title}\n{text}" if title and title not in text else text, False, n.get("from_name") or "Ida",
                notice=True)


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
    return {"id": IDA, "title": a.get("name") or "Ida", "network": "ida", "type": "single", "ida": True, "unread": unread, "running": running,
            "at": last["at"] if last else "", "muted": False,
            "last": {"text": re.sub(r"\s+", " ", last["text"])[:140], "mine": last["mine"], "sender": "", "at": last["at"]} if last else None}


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
                       "ida_url": "https://ida.kulbarts.com" + (f"/chats/{chat_id()}" if chat_id() else "")}]}


@router.get(f"/avatar/{IDA}")
async def ida_avatar():
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


# ---------------- Senden: an den IDA-Chat; die Antwort läuft in IDA weiter, auch wenn die App zu ist ----------------
class IdaSend(BaseModel):
    text: str = Field(max_length=20_000)
    reply_to: str | None = Field(default=None, max_length=300)
    mentions: list = Field(default_factory=list)


async def start(text: str) -> None:
    """Schickt den Text an Ida und kehrt zurück, sobald IDA die Nachricht angenommen hat; die Antwort verfolgt follow()."""
    cid = chat_id()
    body = {"chat_id": cid, "text": text} if cid else {"agent_id": (await agent())["id"], "text": text}
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
            return await start(text)
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
    out += ["", "— Kontext für dich —", f"Beeper-Chat-ID: {f.chat_id}"]
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
            ns, _ = await notices(10)
            fresh = [n for n in ns if _z(n.get("at")) > last] if last else []
            if ns:
                newest = max(_z(n.get("at")) for n in ns)
                if newest != last:
                    last = newest
                    push._kv_set("ida_notice", last)
            if fresh:
                bus.publish({"type": "chat", "id": IDA, "title": "Ida", "new": True})
                if push.has_subs() and not push.muted(IDA) and not bus.count():
                    n = fresh[0]   # die neueste; mehrere: Zahl dazu
                    m = notice_msg(n)
                    push.new_note(IDA)
                    await push.send({"chat": IDA, "from": IDA, "title": n.get("from_name") or "Ida",
                                     "body": re.sub(r"\s+", " ", m["text"])[:300] + (f" (+{len(fresh) - 1} weitere)" if len(fresh) > 1 else ""),
                                     "unread": len(fresh), "at": m["at"], "av": IDA})
        except Exception as e:  # noqa: BLE001 – darf nie sterben
            log.debug("Ida-Postfach: %s", e)
            wait = 120
        await asyncio.sleep(wait)
