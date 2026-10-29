"""Live-Updates: ein In-Memory-Verteiler für die offenen Kalender-Fenster (SSE).

Es gibt nur einen Kalender-Prozess, deshalb reicht das. Wer sich neu verbindet, holt Verpasstes aus `changes`.
"""
import asyncio

_subs: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subs.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subs.discard(q)


def publish(evt: dict) -> None:
    for q in list(_subs):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:  # ein hängendes Fenster darf nichts blockieren; es verbindet sich neu
            _subs.discard(q)


def count() -> int:
    return len(_subs)
