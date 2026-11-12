"""Live: solange die App irgendwo offen ist, fragt der Dienst Gmail alle paar Sekunden nach Änderungen (history.list – billig,
nur die Unterschiede) und sagt den offenen Fenstern Bescheid. Ist keine App offen, passiert nichts (kein Dauer-Abfragen)."""
import asyncio
import logging

from . import bus
from .api import gmail
from .gmail import META_HEADERS, GmailError, NotConnected, header, person

log = logging.getLogger("mail.live")


async def check(state: dict) -> None:
    g = gmail()
    if not state.get("history"):
        state["history"] = str((await g.get("/profile")).get("historyId", ""))
        return
    try:
        d = await g.history(state["history"])
    except GmailError as e:
        if e.status == 404:  # Stand zu alt: neu anfangen, Fenster laden einfach neu
            state["history"] = ""
            bus.publish({"type": "changed"})
            return
        raise
    hist = d.get("history", [])
    state["history"] = str(d.get("historyId") or state["history"])
    if not hist:
        return
    g.forget("labels")
    new = [x["message"] for h in hist for x in h.get("messagesAdded", [])
           if {"INBOX", "UNREAD"} <= set(x["message"].get("labelIds", []))]
    for m in new[:3]:
        try:
            full = await g.get(f"/messages/{m['id']}", format="metadata", metadataHeaders=META_HEADERS)
        except GmailError:
            continue
        hs = full.get("payload", {}).get("headers", [])
        bus.publish({"type": "new", "thread": m.get("threadId"), "from": person(header(hs, "From"))["name"], "subject": header(hs, "Subject")})
    bus.publish({"type": "changed"})


async def loop() -> None:
    state: dict = {}
    while True:
        wait = gmail().s.live_poll_s
        if bus.count():
            try:
                await check(state)
            except NotConnected:
                wait = 60
            except Exception as e:  # noqa: BLE001 – live darf nie sterben
                log.warning("Live-Abgleich: %s", e)
                wait = 30
        else:
            state.pop("history", None)  # beim nächsten Öffnen frisch anfangen (die App lädt dann ohnehin alles neu)
        await asyncio.sleep(wait if bus.count() else 2)
