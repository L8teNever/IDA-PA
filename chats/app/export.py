"""Chats anonym exportieren (Einstellungen → „Chats exportieren“): ausgewählte Chats mit dem ganzen Verlauf, so weit Beeper zurückreicht,
als eine Datei. Statt Namen stehen „User 1“, „User 2“ … (dieselbe Person hat in allen Chats dieselbe Nummer), Chats heißen „Chat 1“, „Chat 2“ …
Namen im Text, Telefonnummern, E-Mail-Adressen und Links (bis auf die Domain) werden ersetzt; Dateinamen, IDs und Pfade kommen nicht mit.
Antworten zeigen, auf welche Nachricht (#Nummer) sie sich beziehen, Reaktionen, von wem sie sind.

Ein langer Verlauf braucht viele Seiten bei Beeper (Minuten) – darum läuft der Export als Auftrag im Hintergrund:
POST /export startet, GET /export/{id} zeigt den Fortschritt, GET /export/{id}/file liefert die fertige Datei."""
import asyncio
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from . import api, store

log = logging.getLogger("ida.chats.export")
router = APIRouter(prefix="/api/v1")

TZ = ZoneInfo("Europe/Berlin")
MAX_CHATS = 50
MAX_PER_CHAT = 100_000     # Sicherung gegen endlose Seiten
MAX_PAGES = 20_000
JOB_TTL = 3600             # fertige Exporte bleiben eine Stunde abrufbar
APPS = {"whatsapp": "WhatsApp", "instagram": "Instagram", "instagramgo": "Instagram", "snapchat": "Snapchat", "telegram": "Telegram",
        "signal": "Signal", "gmessages": "SMS", "discord": "Discord", "facebook": "Messenger", "facebookgo": "Messenger", "messenger": "Messenger",
        "linkedin": "LinkedIn", "twitter": "X", "slack": "Slack", "googlechat": "Google Chat", "imessage": "iMessage", "matrix": "Matrix"}
KIND = {"image": "Bild", "video": "Video", "audio": "Audio", "file": "Datei"}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+", re.I)
PHONE = re.compile(r"(?<![\w+])\+?\d[\d \-/().]{6,}\d(?!\w)")
HANDLE = re.compile(r"(?<![\w@])@[\w.]{2,40}")

_jobs: dict[str, dict] = {}


class ExportReq(BaseModel):
    chats: list[str] = Field(min_length=1, max_length=MAX_CHATS)
    days: int = Field(0, ge=0, le=36500)    # 0 = alles, sonst nur die letzten n Tage
    format: str = Field("txt", pattern="^(txt|json)$")
    me: bool = False                        # True: du heißt „Ich“ statt „User 1“


def app_name(acc: str) -> str:
    k = (acc or "").lower().split(".", 1)[0]   # „whatsapp.49151…“ → „whatsapp“ (die Nummer bleibt draußen)
    return APPS.get(k, k.capitalize() if k and k not in api.BEEPER_ONLY else "Chat")


def local(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        return ts or "?"


def dur(s) -> str:
    try:
        s = int(round(float(s)))
    except (TypeError, ValueError):
        return ""
    return f" {s // 60}:{s % 60:02d}" if s > 0 else ""


class Anon:
    """Vergibt die Decknamen: dieselbe ID (oder derselbe Name) bekommt im ganzen Export dieselbe Nummer."""

    def __init__(self, me_label: str | None):
        self.me_label = me_label or "User 1"
        self.n = 0 if me_label else 1   # ohne „Ich“ ist „User 1“ fest für dich reserviert
        self.ids: dict[str, str] = {}
        self.names: dict[str, str] = {}   # kleingeschriebener Name/Namensteil → Deckname
        self._rx: tuple[int, re.Pattern | None] = (0, None)

    def me(self, *ids: str) -> str:
        for i in ids:
            if i:
                self.ids[i] = self.me_label
        return self.me_label

    def get(self, key: str) -> str:
        if not key:
            key = "?"
        if key not in self.ids:
            self.n += 1
            self.ids[key] = f"User {self.n}"
        return self.ids[key]

    def name(self, full: str, label: str) -> None:
        """Namen einer Person merken, damit er im Text ersetzt wird – ganz und jeder Teil ab 3 Buchstaben (Vorname, Nachname)."""
        full = api.plain(str(full or "")).strip()
        if not full or full.lower() in ("me", "du", "ich") or re.fullmatch(r"[+\d\s\-()]+", full):
            return
        self.names.setdefault(full.lower(), label)
        for part in re.split(r"[\s_.,]+", full):
            if len(part) >= 3 and not part.isdigit():
                self.names.setdefault(part.lower(), label)

    def scrub(self, text: str) -> str:
        if not text:
            return ""
        t = URL.sub(lambda m: f"[Link: {domain(m[0])}]", text)
        t = EMAIL.sub("[E-Mail]", t)
        t = PHONE.sub("[Nummer]", t)
        if self.names:
            if self._rx[0] != len(self.names):   # nur neu bauen, wenn Namen dazugekommen sind
                words = sorted(self.names, key=len, reverse=True)
                self._rx = (len(self.names), re.compile(r"(?<!\w)@?(" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.I))
            t = self._rx[1].sub(lambda m: self.names.get(m[1].lower(), "[Name]"), t)
        return HANDLE.sub("@[Name]", t)   # übrige @Benutzernamen


def domain(u: str) -> str:
    try:
        h = urlparse(u if "://" in u else "https://" + u).hostname or ""
    except ValueError:
        h = ""
    return re.sub(r"^www\.", "", h) or "?"


async def chat_meta(cid: str) -> dict:
    try:
        r = await api.rest_get(f"/v1/chats/{quote(cid, safe='')}")
        return r.json() if r.status_code == 200 else {}
    except (HTTPException, ValueError):
        return {}


async def all_messages(cid: str, since: str, job: dict) -> list[dict]:
    """Den ganzen Verlauf eines Chats Seite für Seite (neueste zuerst), bis Beeper keine älteren mehr hat oder `since` erreicht ist."""
    out, seen, cur, pages = [], set(), "", 0
    while pages < MAX_PAGES and len(out) < MAX_PER_CHAT:
        d = await api.fetch_messages(cid, cur, "before")
        pages += 1
        items = [m for m in d.get("items") or [] if isinstance(m, dict)]
        new = [m for m in items if m.get("id") and m["id"] not in seen]
        if not new:
            break
        for m in new:
            seen.add(m["id"])
        out += new
        job["msgs"] += len(new)
        oldest = min((m.get("timestamp") or "" for m in new), default="")
        nxt = d.get("oldestCursor")
        if not d.get("hasMore") or not nxt or nxt == cur or (since and oldest and oldest < since):
            break
        cur = nxt
    return [m for m in out if not since or (m.get("timestamp") or "") >= since]


async def export_chat(cid: str, label: str, anon: Anon, since: str, job: dict) -> dict:
    members = api.group_members(cid) or [cid]
    members = [m for m in members if not m.startswith("untis:") and m != "ida"]
    if not members:
        return {"chat": label, "app": "", "type": "", "skipped": "WebUntis- und Ida-Chats sind nicht dabei", "messages": []}
    metas = await asyncio.gather(*(chat_meta(m) for m in members))
    single = all((x.get("type") or "single") == "single" for x in metas)
    other = f"grp:{cid}" if len(members) > 1 and single else None   # zusammengeführter Einzelchat: dieselbe Person in jeder App
    apps = list(dict.fromkeys(app_name(str(x.get("accountID") or "")) for x in metas))
    for x in metas:   # Teilnehmer vorab: so werden auch Namen von Leuten ersetzt, die nie selbst schreiben
        for p in (x.get("participants") or {}).get("items") or []:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            lab = anon.me(p["id"]) if p.get("isSelf") else anon.get(other or p["id"])
            anon.ids.setdefault(p["id"], lab)
            for k in ("fullName", "username", "nickname"):
                anon.name(p.get(k) or "", lab)
        if single and not other:
            others = [p for p in (x.get("participants") or {}).get("items") or [] if isinstance(p, dict) and not p.get("isSelf")]
            if len(others) == 1:
                anon.name(x.get("title") or "", anon.ids.get(others[0].get("id"), "[Name]"))

    raw: list[tuple[str, dict]] = []
    for m in members:
        job["step"] = f"{label}: {job['msgs']} Nachrichten"
        try:
            raw += [(m, x) for x in await all_messages(m, since, job)]
        except HTTPException as e:
            log.info("Export %s: %s", m, e.detail)
    raw = [(c, m) for c, m in raw if (m.get("type") or "").upper() != "REACTION" and not api.is_system(m)]
    raw.sort(key=lambda cm: cm[1].get("timestamp") or "")

    def who(c: str, m: dict) -> str:
        sid = m.get("senderID") or ""
        if m.get("isSender"):
            anon.name(m.get("senderName") or "", anon.me_label)
            return anon.me(sid)
        lab = anon.ids.get(sid) or anon.get(other or sid or f"name:{m.get('senderName') or '?'}")
        anon.ids.setdefault(sid, lab)
        anon.name(m.get("senderName") or "", lab)
        return lab

    whos = [who(c, m) for c, m in raw]   # erst alle Namen kennen, dann den Text ersetzen
    shaped = [api.message(m) for _, m in raw]
    try:
        store.sync_transcripts(shaped)   # gespeicherte Umwandlungen der Sprachnachrichten
    except Exception as e:  # noqa: BLE001
        log.info("Umwandlungen nicht lesbar: %s", e)
    num = {(c, m.get("id")): i + 1 for i, (c, m) in enumerate(raw)}
    out = []
    for i, ((c, m), s, w) in enumerate(zip(raw, shaped, whos)):
        files = [{"kind": f["kind"], **({"seconds": int(f["duration"])} if f.get("duration") else {}),
                  **({"transcript": anon.scrub(f["transcript"])} if f.get("transcript") else {})} for f in s["files"]]
        rid = m.get("linkedMessageID")
        reacts = []
        for r in m.get("reactions") or []:
            if isinstance(r, dict) and (k := r.get("reactionKey") or r.get("emoji") or r.get("key")):
                pid = r.get("participantID") or ""
                reacts.append({"key": k, "from": anon.me_label if r.get("isSender") else anon.ids.get(pid) or anon.get(other or pid or "?")})
        out.append({"n": i + 1, "at": m.get("timestamp") or "", "from": w,
                    "text": "" if s["deleted"] else anon.scrub(s["text"]), "files": [] if s["deleted"] else files,
                    "reply_to": num.get((c, rid)) if rid else None, "reply_old": bool(rid) and (c, rid) not in num,
                    "reactions": reacts, "edited": s["edited"], "deleted": s["deleted"]})
        if len(members) > 1:   # zusammengeführt: über welche App die Nachricht kam
            out[-1]["app"] = app_name(str(metas[members.index(c)].get("accountID") or ""))
    return {"chat": label, "app": ", ".join(apps), "type": "Einzelchat" if single else "Gruppe", "messages": out}


def body(m: dict) -> str:
    if m["deleted"]:
        return "[gelöscht]"
    parts = []
    for f in m["files"]:
        tag = "Sprachnachricht" if f["kind"] == "audio" and "transcript" in f else KIND.get(f["kind"], "Datei")
        parts.append(f"[{tag}{dur(f.get('seconds'))}]" + (f" „{f['transcript']}“" if f.get("transcript") else ""))
    if m["text"]:
        parts.append(m["text"])
    return " ".join(parts) or "[leer]"


def to_text(chats: list[dict], days: int, me: bool) -> str:
    now = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")
    head = [f"Anonymer Chat-Export – erstellt {now}" + (f", letzte {days} Tage" if days else ", ganzer Verlauf"),
            ("Du heißt „Ich“" if me else "Du bist „User 1“") + ", alle anderen „User 2“, „User 3“ … – dieselbe Person hat in allen Chats dieselbe Nummer.",
            "Namen, Telefonnummern, E-Mail-Adressen und Links (bis auf die Domain) sind ersetzt. „↩ #12“ heißt: Antwort auf Nachricht 12.", ""]
    out = head
    for c in chats:
        ms = c["messages"]
        span = f" ({local(ms[0]['at'])[:10]} – {local(ms[-1]['at'])[:10]})" if ms else ""
        out += ["", f"=== {c['chat']} · {c['app'] or '–'} · {c['type'] or '–'} · {len(ms)} Nachrichten{span} ===", ""]
        if c.get("skipped"):
            out.append(f"({c['skipped']})")
        by_n = {m["n"]: m for m in ms}
        for m in ms:
            re_ = ""
            if m["reply_to"] and (t := by_n.get(m["reply_to"])):
                q = re.sub(r"\s+", " ", body(t))
                re_ = f" ↩ #{t['n']} ({t['from']}: „{q[:60]}{'…' if len(q) > 60 else ''}“)"
            elif m["reply_old"]:
                re_ = " ↩ (auf eine ältere Nachricht)"
            app = f" ({m['app']})" if m.get("app") else ""
            line = f"#{m['n']} [{local(m['at'])}]{app} {m['from']}{re_}: {body(m)}{' (bearbeitet)' if m['edited'] and not m['deleted'] else ''}"
            out.append(line)
            if m["reactions"]:
                out.append("    Reaktionen: " + ", ".join(f"{r['key']} {r['from']}" for r in m["reactions"]))
    return "\n".join(out) + "\n"


async def run(job: dict, req: ExportReq) -> None:
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=req.days)).strftime("%Y-%m-%dT%H:%M:%SZ") if req.days else ""
        anon = Anon("Ich" if req.me else None)
        chats = []
        for i, cid in enumerate(req.chats):
            job["chat"] = i + 1
            chats.append(await export_chat(cid, f"Chat {i + 1}", anon, since, job))
        stamp = datetime.now(TZ).strftime("%Y-%m-%d")
        if req.format == "json":
            data = json.dumps({"created": datetime.now(TZ).isoformat(timespec="seconds"), "days": req.days or None,
                               "note": "Anonym: User 1, User 2 … statt Namen; reply_to = Nummer (n) der Nachricht, auf die geantwortet wurde",
                               "chats": chats}, ensure_ascii=False, indent=1)
            job.update(name=f"chats-anonym-{stamp}.json", ct="application/json; charset=utf-8")
        else:
            data = to_text(chats, req.days, req.me)
            job.update(name=f"chats-anonym-{stamp}.txt", ct="text/plain; charset=utf-8")
        job.update(data=data.encode(), state="done", step="Fertig", total=sum(len(c["messages"]) for c in chats))
    except Exception as e:  # noqa: BLE001 – im Fortschritt zeigen statt still abbrechen
        log.warning("Export ging nicht: %s", e)
        job.update(state="error", error=str(getattr(e, "detail", e))[:300])


def _clean() -> None:
    for k in [k for k, j in _jobs.items() if time.time() - j["t"] > JOB_TTL]:
        _jobs.pop(k, None)


def _view(jid: str, j: dict) -> dict:
    return {"id": jid, "state": j["state"], "chat": j["chat"], "chats": j["chats"], "msgs": j["msgs"], "step": j["step"],
            "error": j.get("error", ""), "total": j.get("total"), "name": j.get("name", ""), "size": len(j.get("data") or b"")}


@router.post("/export")
async def start(req: ExportReq):
    for c in req.chats:
        api.check_ids(c)
    _clean()
    if sum(1 for j in _jobs.values() if j["state"] == "running") >= 2:
        raise HTTPException(429, "Es laufen schon zwei Exporte – warte kurz")
    jid = secrets.token_urlsafe(12)
    job = {"t": time.time(), "state": "running", "chat": 0, "chats": len(req.chats), "msgs": 0, "step": "Startet …"}
    _jobs[jid] = job
    job["task"] = asyncio.create_task(run(job, req))
    return _view(jid, job)


@router.get("/export/{jid}")
async def status(jid: str):
    j = _jobs.get(jid)
    if not j:
        raise HTTPException(404, "Export nicht gefunden (abgelaufen?)")
    return _view(jid, j)


@router.get("/export/{jid}/file")
async def file(jid: str, inline: bool = False):
    j = _jobs.get(jid)
    if not j or j["state"] != "done":
        raise HTTPException(404, "Export nicht fertig oder abgelaufen")
    disp = "inline" if inline else "attachment"
    return Response(j["data"], media_type=j["ct"], headers={"content-disposition": f'{disp}; filename="{j["name"]}"', "cache-control": "no-store"})
