"""Live: solange die App irgendwo offen ist, fragt der Dienst Beeper alle paar Sekunden nach den zuletzt aktiven Chats und sagt den
offenen Fenstern Bescheid, wenn sich einer geändert hat (neue Aktivität oder Ungelesen-Zahl). Ohne offene App fragt er nur selten
(ARCHIVE_POLL_S) – dann nur, um neue Nachrichten zu merken (store.py). Ist ein Handy für Benachrichtigungen angemeldet, fragt er
alle PUSH_POLL_S Sekunden und schickt bei neuen ungelesenen Nachrichten eine Meldung (push.py)."""
import asyncio
import logging
import re

from . import api, bus, push, store
from .api import archive, beeper, parse_chats
from .config import get_settings

log = logging.getLogger("chats.live")


async def remember(ids: list[str]) -> None:
    """Geänderte Chats einmal laden, damit ihre Nachrichten gemerkt sind – auch wenn sie später jemand löscht."""
    for i in ids:
        try:
            await archive(i)
        except Exception as e:  # noqa: BLE001
            log.debug("Merken %s: %s", i, e)


async def check(state: dict) -> None:
    items, _ = parse_chats(await beeper().text("search_chats", {"limit": 30, "inbox": "primary"}))
    sig = {c["id"]: (c["at"], c["unread"]) for c in items}
    old = state.get("sig")
    state["sig"] = sig
    if get_settings().archive_poll_s > 0 and not (state.get("job") and not state["job"].done()):
        changed = [c["id"] for c in items if old is None or old.get(c["id"], ("",))[0] != c["at"]]
        if changed:
            state["job"] = asyncio.create_task(remember(changed))
    if old is None:
        return
    fresh: list[dict] = []
    ar = push.autoreads()
    for c in items:
        before = old.get(c["id"])
        if before != sig[c["id"]]:
            more_unread = c["unread"] > (before[1] if before else 0)
            if more_unread and push.autoread_of(c["id"], ar) == "always":   # sofort gelesen: keine Meldung, in Beeper gelesen
                try:
                    await api.beeper_read(c['id'])
                    c["unread"] = 0
                    sig[c["id"]] = (c["at"], 0)
                    bus.publish({"type": "chat", "id": c["id"], "title": c["title"], "new": False, "unread": 0, "at": c["at"]})
                    continue
                except Exception as e:  # noqa: BLE001
                    log.warning("Automatisch gelesen %s: %s", c["id"], e)
            bus.publish({"type": "chat", "id": c["id"], "title": c["title"], "new": more_unread, "unread": c["unread"], "at": c["at"]})
            if more_unread:
                fresh.append(c)
    if fresh and push.has_subs():
        await notify(fresh)


def _what(p: dict) -> str:
    """Text der Meldung; ohne Text ein Hinweis auf den Anhang."""
    text = re.sub(r"\s+", " ", api.plain(p.get("text") or "")).strip()
    if text:
        return text[:300]
    att = [a for a in p.get("attachments") or [] if isinstance(a, dict)]
    kinds = [api.kind(a) for a in att]
    t = (p.get("type") or "").upper()
    if any(a.get("isVoiceNote") for a in att) or t == "VOICE":
        return "🎤 Sprachnachricht"
    if "video" in kinds or t == "VIDEO":
        return "🎥 Video"
    if any(a.get("isSticker") for a in att) or t == "STICKER":
        return "Sticker"
    if "image" in kinds or t == "IMAGE":
        return "📷 Foto"
    if kinds or t in ("FILE", "AUDIO", "MEDIA"):
        return "📎 Datei"
    return "Neue Nachricht"


async def notify(chats: list[dict]) -> None:
    """Eine Meldung pro Chat mit neuen Nachrichten – außer stummgeschaltet (hier oder in Beeper)."""
    m = push.mutes()
    chats = [c for c in chats if not c.get("muted") and not push.muted(c["id"], m)]
    if not chats:
        return
    api._chats_cache = None   # Vorschau ganz frisch holen
    try:
        prev = {k: c.get("preview") for c in await api.rest_chats() for k in (c.get("localChatID"), c.get("id")) if k}
    except Exception:  # noqa: BLE001 – ohne Vorschau trotzdem melden
        prev = {}
    metas = store.metas()
    for c in chats:
        p = prev.get(c["id"]) if isinstance(prev.get(c["id"]), dict) else {}
        if p.get("isSender"):
            continue
        grp = store.group_of(c["id"])
        title = (metas.get(grp, {}).get("name") if grp else "") or c["title"]
        who = (p.get("senderName") or "").strip() if c.get("type") == "group" else ""
        body = _what(p) if p else ("Neue Nachricht" if c["unread"] <= 1 else f"{c['unread']} neue Nachrichten")
        try:
            await push.send({"chat": grp or c["id"], "from": c["id"], "title": title[:80], "body": f"{who}: {body}" if who else body,
                             "unread": c["unread"], "at": p.get("timestamp") or c["at"],
                             "av": (metas.get(grp, {}).get("avatar") if grp else "") or c["id"]})
        except Exception as e:  # noqa: BLE001
            log.warning("Push für %s: %s", c["id"], e)


async def loop() -> None:
    state: dict = {}
    while True:
        s = get_settings()
        # App offen: oft; App zu, aber Handy für Benachrichtigungen angemeldet: etwas seltener; sonst nur zum Merken
        wait = s.live_poll_s if bus.count() else s.push_poll_s if s.push_poll_s > 0 and push.has_subs() else s.archive_poll_s
        if wait > 0:
            try:
                await check(state)
            except Exception as e:  # noqa: BLE001 – live darf nie sterben
                log.warning("Live-Abgleich: %s", e)
                wait = max(wait, 30)
        else:
            state.pop("sig", None)
        # App geht auf, während selten abgefragt wird: nicht die ganze Pause abwarten
        end = asyncio.get_running_loop().time() + (wait if wait > 0 else 2)
        open_before = bool(bus.count())
        while asyncio.get_running_loop().time() < end and bool(bus.count()) == open_before:
            await asyncio.sleep(min(2, max(0.05, end - asyncio.get_running_loop().time())))
