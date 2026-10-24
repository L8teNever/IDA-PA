"""Background runs (routines, agent wake-ups, memory upkeep) strictly one after another.

Subscription limits are per account and runs share the sandbox, so parallel runs would only hurt.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("ida.jobs")

_queue: asyncio.Queue | None = None
_worker: asyncio.Task | None = None
_current: str | None = None


def _q() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def _work() -> None:
    global _current
    q = _q()
    while True:
        label, fn, fut = await q.get()
        _current = label
        try:
            res = await fn()
            if not fut.done():
                fut.set_result(res)
        except Exception as e:  # a failing job must not stop the queue
            log.exception("job %s failed", label)
            if not fut.done():
                fut.set_exception(e)
        finally:
            _current = None
            q.task_done()


def start() -> None:
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.get_running_loop().create_task(_work())


async def stop() -> None:
    global _worker, _queue
    if _worker:
        _worker.cancel()
        try:
            await _worker
        except (asyncio.CancelledError, Exception):
            pass
    _worker, _queue = None, None


def submit(label: str, fn: Callable[[], Awaitable[Any]]) -> asyncio.Future:
    """Queue a job; the returned future resolves with its result (awaiting it is optional)."""
    start()
    fut = asyncio.get_running_loop().create_future()
    fut.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)  # avoid "never retrieved" warnings
    _q().put_nowait((label, fn, fut))
    return fut


def status() -> dict:
    return {"running": _current, "waiting": _q().qsize()}
