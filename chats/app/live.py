"""Live: solange die App irgendwo offen ist, fragt der Dienst Beeper alle paar Sekunden nach den zuletzt aktiven Chats und sagt den
offenen Fenstern Bescheid, wenn sich einer geändert hat (neue Aktivität oder Ungelesen-Zahl). Ohne offene App fragt er nur selten
(ARCHIVE_POLL_S) – dann nur, um neue Nachrichten zu merken (store.py)."""
import asyncio
import logging

from . import bus
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
    for c in items:
        before = old.get(c["id"])
        if before != sig[c["id"]]:
            more_unread = c["unread"] > (before[1] if before else 0)
            bus.publish({"type": "chat", "id": c["id"], "title": c["title"], "new": more_unread, "unread": c["unread"], "at": c["at"]})


async def loop() -> None:
    state: dict = {}
    while True:
        s = get_settings()
        wait = s.live_poll_s if bus.count() else s.archive_poll_s   # App zu: seltener, nur zum Merken
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
