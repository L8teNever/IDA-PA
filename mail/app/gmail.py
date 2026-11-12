"""Gmail-REST-API (https://developers.google.com/gmail/api/reference/rest) – asynchron, ohne Google-Bibliothek.

Den Zugang (Access-Token, nur Gmail-Scopes) holt der Dienst beim Dienst "google" (POST /api/v1/token/gmail); die Anmeldung bei
Google liegt dort. Läuft ein Token ab (401), wird einmal ein frischer geholt.
"""
from __future__ import annotations

import asyncio
import base64
import random
import email.utils
import re
import time
from email.message import EmailMessage

import httpx

from .config import get_settings


class NotConnected(Exception):
    """Google ist (noch) nicht verbunden – Anmeldung in IDA unter Einstellungen → Google."""


class GmailError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


SYSTEM = [("INBOX", "Posteingang", "inbox"), ("STARRED", "Markiert", "star"), ("IMPORTANT", "Wichtig", "label_important"),
          ("SENT", "Gesendet", "send"), ("DRAFT", "Entwürfe", "draft"), ("SPAM", "Spam", "report"), ("TRASH", "Papierkorb", "delete")]
META_HEADERS = ["From", "To", "Subject", "Date"]


def b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def header(headers: list[dict], name: str) -> str:
    n = name.lower()
    return next((h.get("value", "") for h in headers or [] if h.get("name", "").lower() == n), "")


def person(value: str) -> dict:
    name, addr = email.utils.parseaddr(value or "")
    return {"name": name or addr.split("@")[0], "email": addr}


def people(value: str) -> list[dict]:
    return [{"name": n or a.split("@")[0], "email": a} for n, a in email.utils.getaddresses([value or ""]) if a]


RETRIES = 4


def backoff(attempt: int, retry_after: str | None = None) -> float:
    """0,4 s, 0,8 s, 1,6 s, 3,2 s (+ etwas Zufall, damit nicht alle gleichzeitig wiederkommen); Retry-After von Google zählt, höchstens 5 s."""
    try:
        if retry_after:
            return min(5.0, float(retry_after))
    except ValueError:
        pass
    return 0.4 * 2 ** attempt + random.random() * 0.2


def rate_limited(r: httpx.Response) -> bool:
    try:
        errs = r.json().get("error", {}).get("errors", [])
    except ValueError:
        return False
    return any(e.get("reason") in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded") for e in errs)


class Gmail:
    def __init__(self):
        self.s = get_settings()
        self.http = httpx.AsyncClient(timeout=30)
        self._token, self._exp = "", 0.0
        self._lock = asyncio.Lock()
        self._cache: dict[str, tuple[float, object]] = {}
        # Gmail erlaubt pro Nutzer nur begrenzt viele gleichzeitige Anfragen ("Too many concurrent requests" → 429/403).
        # Darum laufen alle Anfragen des Dienstes zusammen über diese Schranke – auch wenn mehrere Listen gleichzeitig laden.
        self._gate = asyncio.Semaphore(max(1, self.s.gmail_concurrency))
        self._meta: dict[str, dict] = {}   # Thread-Kopfdaten nach (id, historyId) – ändert sich der Thread, ändert sich die historyId

    async def close(self) -> None:
        await self.http.aclose()

    # ---------------- Zugang ----------------
    async def token(self, fresh: bool = False) -> str:
        async with self._lock:
            if fresh or not self._token or time.time() > self._exp:
                h = {"authorization": f"Bearer {self.s.google_api_token}"} if self.s.google_api_token else {}
                try:
                    r = await self.http.post(f"{self.s.google_api_url.rstrip('/')}/api/v1/token/gmail", headers=h)
                except httpx.HTTPError as e:
                    raise NotConnected(f"Google-Dienst nicht erreichbar ({e.__class__.__name__})") from e
                if r.status_code == 409:
                    raise NotConnected(r.json().get("detail") or "Google ist nicht verbunden")
                if r.status_code >= 400:
                    raise NotConnected(f"Google-Dienst: HTTP {r.status_code}")
                d = r.json()
                self._token, self._exp = d["access_token"], time.time() + min(float(d.get("expires_in", 3000)), 3000) - 60
            return self._token

    async def req(self, method: str, path: str, *, upload: bool = False, **kw) -> httpx.Response:
        base = self.s.gmail_upload_url if upload else self.s.gmail_api_url
        fresh = False
        for attempt in range(RETRIES + 1):
            try:
                async with self._gate:
                    r = await self.http.request(method, base + path, headers={"authorization": f"Bearer {await self.token(fresh)}"}, **kw)
            except httpx.TransportError as e:   # Verbindung kurz weg / Zeitüberschreitung
                if attempt == RETRIES:
                    raise GmailError(503, f"Gmail nicht erreichbar ({e.__class__.__name__})") from e
                await asyncio.sleep(backoff(attempt))
                continue
            if r.status_code == 401 and not fresh:
                fresh = True
                continue
            if attempt < RETRIES and (r.status_code in (429, 500, 502, 503, 504) or (r.status_code == 403 and rate_limited(r))):
                await asyncio.sleep(backoff(attempt, r.headers.get("retry-after")))
                continue
            break
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message") or r.text
            except ValueError:
                msg = r.text
            status = 429 if r.status_code == 403 and rate_limited(r) else r.status_code
            raise GmailError(status, str(msg)[:300])
        return r

    async def get(self, path: str, **params) -> dict:
        return (await self.req("GET", path, params={k: v for k, v in params.items() if v not in (None, "")})).json()

    async def cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = await fn()
        self._cache[key] = (time.time(), val)
        return val

    def forget(self, *prefixes: str) -> None:
        if not prefixes:
            self._meta.clear()
        for k in list(self._cache):
            if not prefixes or k.startswith(prefixes):
                self._cache.pop(k, None)

    # ---------------- Konto ----------------
    async def profile(self) -> dict:
        return await self.cached("profile", 300, lambda: self.get("/profile"))

    async def send_as(self) -> list[dict]:
        async def load():
            try:
                d = await self.get("/settings/sendAs")
            except GmailError:
                return []
            return [{"email": x["sendAsEmail"], "name": x.get("displayName", ""), "default": bool(x.get("isDefault"))}
                    for x in d.get("sendAs", []) if x.get("verificationStatus", "accepted") == "accepted" or x.get("isPrimary")]
        return await self.cached("sendas", 600, load)

    # ---------------- Adressen ----------------
    async def contacts(self) -> list[dict]:
        """Wer schreibt dir / wem schreibst du: aus Kopfzeilen der letzten gesendeten und empfangenen Mails, nach Häufigkeit."""
        async def load():
            me = ((await self.profile()).get("emailAddress") or "").lower()
            ids: list[tuple[str, bool]] = []
            for label, n in (("SENT", 120), ("INBOX", 80)):
                try:
                    ids += [(m["id"], label == "SENT") for m in (await self.get("/messages", labelIds=label, maxResults=n)).get("messages", [])]
                except GmailError:
                    pass
            sem = asyncio.Semaphore(8)

            async def one(i):
                async with sem:
                    try:
                        return await self.get(f"/messages/{i}", format="metadata", metadataHeaders=["From", "To", "Cc"])
                    except GmailError:
                        return {}
            found: dict[str, dict] = {}
            for n, m in enumerate(await asyncio.gather(*(one(i) for i, _ in ids))):
                sent = ids[n][1]
                hs = m.get("payload", {}).get("headers", [])
                for name in (("To", "Cc") if sent else ("From",)):
                    for p in people(header(hs, name)):
                        a = p["email"].lower()
                        if not a or a == me or "noreply" in a or "no-reply" in a:
                            continue
                        c = found.setdefault(a, {"email": p["email"], "name": "", "n": 0})
                        c["n"] += 3 if sent else 1
                        if p["name"] and "@" not in p["name"] and p["name"] != a.split("@")[0]:
                            c["name"] = p["name"]
            return sorted(found.values(), key=lambda c: -c["n"])[:400]
        return await self.cached("contacts", 1800, load)

    # ---------------- Ordner (Labels) ----------------
    async def labels(self) -> list[dict]:
        async def load():
            all_ = (await self.get("/labels")).get("labels", [])
            user = sorted((x for x in all_ if x.get("type") == "user" and x.get("labelListVisibility") != "labelHide"), key=lambda x: x["name"].lower())
            wanted = [i for i, _, _ in SYSTEM if any(x["id"] == i for x in all_)] + [x["id"] for x in user]
            sem = asyncio.Semaphore(8)

            async def one(i):
                async with sem:
                    return await self.get(f"/labels/{i}")
            full = {d["id"]: d for d in await asyncio.gather(*(one(i) for i in wanted))}
            out = [{"id": i, "name": n, "icon": ic, "system": True, "unread": full[i].get("threadsUnread", 0), "total": full[i].get("threadsTotal", 0)}
                   for i, n, ic in SYSTEM if i in full]
            out += [{"id": x["id"], "name": x["name"], "icon": "label", "system": False, "color": (x.get("color") or {}).get("backgroundColor"),
                     "unread": full[x["id"]].get("threadsUnread", 0), "total": full[x["id"]].get("threadsTotal", 0)} for x in user]
            return out
        return await self.cached("labels", 20, load)

    # ---------------- Konversationen ----------------
    async def threads(self, label: str = "INBOX", q: str = "", page: str = "", size: int = 30) -> dict:
        params: dict = {"maxResults": size, "pageToken": page, "q": q}
        if label and label != "ALL":
            params["labelIds"] = label
        if label in ("SPAM", "TRASH"):
            params["includeSpamTrash"] = "true"
        d = await self.get("/threads", **params)
        me = (await self.profile()).get("emailAddress", "").lower()
        async def one(t):
            key = f"{t['id']}:{t.get('historyId', '')}"
            if key not in self._meta:
                full = await self.get(f"/threads/{t['id']}", format="metadata", metadataHeaders=META_HEADERS)
                if len(self._meta) > 2000:
                    self._meta.pop(next(iter(self._meta)))
                self._meta[key] = summary(full, me)
            return self._meta[key]
        items = await asyncio.gather(*(one(t) for t in d.get("threads", [])))
        return {"items": [i for i in items if i], "next": d.get("nextPageToken"), "estimate": d.get("resultSizeEstimate", 0)}

    async def thread(self, tid: str) -> dict:
        d = await self.get(f"/threads/{tid}", format="full")
        msgs = []
        for m in d.get("messages", []):
            msgs.append(await self.message_view(m))
        drafts = {}
        if any("DRAFT" in m.get("labelIds", []) for m in d.get("messages", [])):
            drafts = {x["message"]["id"]: x["id"] for x in (await self.get("/drafts", maxResults=200)).get("drafts", [])}
        for m in msgs:
            m["draft_id"] = drafts.get(m["id"])
        labels = sorted({l for m in d.get("messages", []) for l in m.get("labelIds", [])})
        return {"id": d["id"], "subject": msgs[0]["subject"] if msgs else "", "labels": labels, "messages": msgs,
                "unread": "UNREAD" in labels, "starred": "STARRED" in labels}

    async def message_view(self, m: dict) -> dict:
        pl = m.get("payload", {})
        hs = pl.get("headers", [])
        text, html, atts, inline = [], [], [], {}

        async def walk(p):
            mt, body, fn = p.get("mimeType", ""), p.get("body") or {}, p.get("filename") or ""
            cid = header(p.get("headers", []), "Content-ID").strip("<>")
            if fn or (body.get("attachmentId") and not mt.startswith("text/")):
                a = {"id": body.get("attachmentId", ""), "name": fn or "anhang", "type": mt, "size": body.get("size", 0)}
                if body.get("data") and not a["id"]:
                    a["data"] = body["data"]
                if cid:
                    inline[cid] = a["id"]
                if not (cid and mt.startswith("image/") and "inline" in header(p.get("headers", []), "Content-Disposition").lower()):
                    atts.append(a)
                elif cid:
                    a["inline"] = True
            elif mt in ("text/plain", "text/html"):
                raw = body.get("data")
                if not raw and body.get("attachmentId"):  # große Textteile liegen wie Anhänge vor
                    raw = (await self.get(f"/messages/{m['id']}/attachments/{body['attachmentId']}")).get("data", "")
                if raw:
                    charset = (re.search(r'charset="?([\w-]+)', header(p.get("headers", []), "Content-Type")) or [None, "utf-8"])[1]
                    try:
                        val = b64d(raw).decode(charset, errors="replace")
                    except LookupError:
                        val = b64d(raw).decode("utf-8", errors="replace")
                    (html if mt == "text/html" else text).append(val)
            for c in p.get("parts") or []:
                await walk(c)
        await walk(pl)
        labels = m.get("labelIds", [])
        return {"id": m["id"], "thread_id": m.get("threadId"), "from": person(header(hs, "From")), "to": people(header(hs, "To")),
                "cc": people(header(hs, "Cc")), "reply_to": header(hs, "Reply-To"), "subject": header(hs, "Subject"),
                "date": int(m.get("internalDate", 0)), "snippet": m.get("snippet", ""), "unread": "UNREAD" in labels,
                "starred": "STARRED" in labels, "labels": labels, "message_id": header(hs, "Message-ID"),
                "references": header(hs, "References"), "text": "\n".join(text), "html": "\n".join(html),
                "attachments": [a for a in atts if a.get("id")], "inline": inline}

    async def attachment(self, mid: str, aid: str) -> bytes:
        return b64d((await self.get(f"/messages/{mid}/attachments/{aid}")).get("data", ""))

    async def modify(self, tid: str, add: list[str], remove: list[str]) -> None:
        await self.req("POST", f"/threads/{tid}/modify", json={"addLabelIds": add, "removeLabelIds": remove})
        self.forget("labels")

    async def trash(self, tid: str, undo: bool = False) -> None:
        await self.req("POST", f"/threads/{tid}/{'untrash' if undo else 'trash'}")
        self.forget("labels")

    # ---------------- Schreiben ----------------
    async def send(self, raw: bytes, thread_id: str = "") -> dict:
        body = {"raw": base64.urlsafe_b64encode(raw).decode()}
        if thread_id:
            body["threadId"] = thread_id
        r = (await self.req("POST", "/messages/send", json=body)).json()
        self.forget("labels")
        return r

    async def save_draft(self, raw: bytes, thread_id: str = "", draft_id: str = "") -> dict:
        msg = {"raw": base64.urlsafe_b64encode(raw).decode()}
        if thread_id:
            msg["threadId"] = thread_id
        if draft_id:
            r = await self.req("PUT", f"/drafts/{draft_id}", json={"id": draft_id, "message": msg})
        else:
            r = await self.req("POST", "/drafts", json={"message": msg})
        self.forget("labels")
        return r.json()

    async def delete_draft(self, draft_id: str) -> None:
        await self.req("DELETE", f"/drafts/{draft_id}")
        self.forget("labels")

    # ---------------- Änderungen (für live) ----------------
    async def history(self, start: str) -> dict:
        return await self.get("/history", startHistoryId=start, maxResults=100)


def summary(t: dict, me: str) -> dict | None:
    msgs = t.get("messages", [])
    if not msgs:
        return None
    last = msgs[-1]
    hs0 = msgs[0].get("payload", {}).get("headers", [])
    names = []
    for m in msgs:
        p = person(header(m.get("payload", {}).get("headers", []), "From"))
        n = "Ich" if p["email"].lower() == me else p["name"]
        if n not in names:
            names.append(n)
    labels = {l for m in msgs for l in m.get("labelIds", [])}
    return {"id": t["id"], "subject": header(hs0, "Subject"), "from": names[-3:], "snippet": last.get("snippet", ""),
            "date": int(last.get("internalDate", 0)), "count": len(msgs), "unread": "UNREAD" in labels, "starred": "STARRED" in labels,
            "draft": "DRAFT" in labels, "labels": sorted(labels)}


def build_raw(*, sender: str, to: str, cc: str = "", bcc: str = "", subject: str = "", text: str = "", in_reply_to: str = "",
              references: str = "", files: list[tuple[str, str, bytes]] | None = None) -> bytes:
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
    msg.set_content(text or "")
    for name, ctype, data in files or []:
        main, _, sub = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=main or "application", subtype=sub or "octet-stream", filename=name)
    return msg.as_bytes()
