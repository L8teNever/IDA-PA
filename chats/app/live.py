"""Live: solange die App irgendwo offen ist, fragt der Dienst Beeper alle paar Sekunden nach den zuletzt aktiven Chats und sagt den
offenen Fenstern Bescheid, wenn sich einer geändert hat (neue Aktivität oder Ungelesen-Zahl). Ohne offene App wird nichts abgefragt."""
import asyncio
import logging

from . import bus
from .api import beeper, parse_chats
from .config import get_settings
from .mcp import BeeperError

log = logging.getLogger("chats.live")


async def check(state: dict) -> None:
    items, _ = parse_chats(await beeper().text("search_chats", {"limit": 30, "inbox": "primary"}))
    sig = {c["id"]: (c["at"], c["unread"]) for c in items}
    old = state.get("sig")
    state["sig"] = sig
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
        wait = get_settings().live_poll_s
        if bus.count():
            try:
                await check(state)
            except BeeperError as e:
                log.warning("Live-Abgleich: %s", e)
                wait = 30
            except Exception as e:  # noqa: BLE001 – live darf nie sterben
                log.warning("Live-Abgleich: %s", e)
                wait = 30
        else:
            state.pop("sig", None)
        await asyncio.sleep(wait if bus.count() else 2)
