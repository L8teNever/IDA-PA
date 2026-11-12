"""Live-Updates an die offenen Fenster (Server-Sent Events, ein Prozess, im Speicher)."""
import asyncio

_subs: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subs.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subs.discard(q)


def publish(evt: dict) -> None:
    for q in list(_subs):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            _subs.discard(q)


def count() -> int:
    return len(_subs)
