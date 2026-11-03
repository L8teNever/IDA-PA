"""Warteschlange eines Chats (Ida Code): Nachrichten, die geschrieben wurden, während die KI noch arbeitet.
Sie liegt auf dem Server – Seite zu, Thread gewechselt oder anderes Gerät: nichts geht verloren, und der Server
schickt die nächste selbst ab, sobald die laufende Antwort fertig ist. Hat der Nutzer bewusst gestoppt, wartet
die Schlange ("pausiert"), bis er wieder etwas sendet oder "Jetzt senden" drückt."""
import asyncio
import itertools
import time

from ..db import session_scope
from . import settings_store

MAX_ITEMS = 50
_locks: dict[str, asyncio.Lock] = {}
_steer: set[str] = set()  # "Jetzt senden" gedrückt: der Stopp dadurch soll die Schlange nicht anhalten
_ids = itertools.count(int(time.time() * 1000))


def key(chat_id: str) -> str:
    return f"code_queue:{chat_id}"


def lock(chat_id: str) -> asyncio.Lock:
    return _locks.setdefault(chat_id, asyncio.Lock())


async def get(db, chat_id: str) -> dict:
    d = await settings_store.get(db, key(chat_id)) or {}
    return {"items": list(d.get("items") or []), "error": d.get("error")}


async def _put(db, chat_id: str, items: list[dict], error: str | None = None) -> dict:
    if items or error:
        await settings_store.put(db, key(chat_id), {"items": items, "error": error})
    else:
        await settings_store.delete(db, key(chat_id))
    return {"items": items, "error": error}


async def add(db, chat_id: str, item: dict) -> dict:
    q = await get(db, chat_id)
    if len(q["items"]) >= MAX_ITEMS:
        from .errors import Invalid
        raise Invalid("Die Warteschlange ist voll")
    return await _put(db, chat_id, q["items"] + [{"id": str(next(_ids)), **item}])


async def remove(db, chat_id: str, item_id: str) -> dict:
    q = await get(db, chat_id)
    return await _put(db, chat_id, [x for x in q["items"] if x["id"] != item_id])


async def to_front(db, chat_id: str, item_id: str) -> bool:
    q = await get(db, chat_id)
    it = next((x for x in q["items"] if x["id"] == item_id), None)
    if not it:
        return False
    await _put(db, chat_id, [it] + [x for x in q["items"] if x is not it])
    return True


async def clear(db, chat_id: str) -> None:
    _steer.discard(chat_id)
    await settings_store.delete(db, key(chat_id))


def steer(chat_id: str) -> None:
    _steer.add(chat_id)


async def take(chat_id: str, user_stopped: bool) -> dict | None:
    """Eine Antwort ist zu Ende: die nächste Nachricht aus der Schlange (und raus damit) – oder None.
    Nach einem bewussten Stopp bleibt die Schlange stehen, außer der Stopp kam von "Jetzt senden"."""
    steered = chat_id in _steer
    _steer.discard(chat_id)
    if user_stopped and not steered:
        return None
    async with session_scope() as db:
        q = await get(db, chat_id)
        if not q["items"]:
            return None
        await _put(db, chat_id, q["items"][1:])
        return q["items"][0]


async def put_back(chat_id: str, item: dict, error: str) -> None:
    """Die Nachricht konnte nicht starten: sie kommt wieder nach vorne, mit dem Grund."""
    async with session_scope() as db:
        q = await get(db, chat_id)
        await _put(db, chat_id, [item] + q["items"], error[:300])
