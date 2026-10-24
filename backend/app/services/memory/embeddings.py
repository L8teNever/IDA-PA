"""Optional embeddings from a local Ollama (for search by meaning). Without a model the memory falls back
to keyword search; embeddings are filled in the background and never block a run."""
import array
import asyncio
import logging
import math
import time

import httpx
from sqlalchemy import or_, select

from ...config import get_settings
from ...db import session_scope
from ...models import MemoryEntry

log = logging.getLogger("ida.memory")
PREFER = ["nomic-embed-text", "mxbai-embed-large", "bge-m3", "snowflake-arctic-embed", "embeddinggemma", "qwen3-embedding", "all-minilm"]
MIN_COSINE = 0.5

_model: str | None = None
_detected_at = 0.0
_lock = asyncio.Lock()
_pending: asyncio.Task | None = None


async def model() -> str | None:
    """Configured model, or the first known embedding model installed in Ollama (checked once a minute)."""
    global _model, _detected_at
    s = get_settings()
    if s.embed_model:
        return s.embed_model
    if time.time() - _detected_at < 60:
        return _model
    _detected_at = time.time()
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            names = [m["name"] for m in (await c.get(s.ollama_url + "/api/tags")).json().get("models", [])]
        _model = next((n for p in PREFER for n in names if n == p or n.startswith(p + ":")), None)
    except (httpx.HTTPError, ValueError, KeyError):
        _model = None
    return _model


def _norm(v: list[float]) -> array.array:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return array.array("f", (x / n for x in v))


def to_bytes(v: array.array) -> bytes:
    return v.tobytes()


def from_bytes(b: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(b)
    return a


def cosine(a: array.array, b: array.array) -> float:
    return sum(x * y for x, y in zip(a, b))


async def embed(texts: list[str]) -> tuple[str, list[array.array]] | None:
    m = await model()
    if not m:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(get_settings().ollama_url + "/api/embed", json={"model": m, "input": texts})
        vecs = r.json().get("embeddings")
        if not isinstance(vecs, list) or len(vecs) != len(texts):
            return None
        return m, [_norm(v) for v in vecs]
    except (httpx.HTTPError, ValueError):
        return None


async def backfill() -> None:
    """Computes missing embeddings (new/changed entries or a different model)."""
    async with _lock:
        m = await model()
        if not m:
            return
        for _ in range(50):
            async with session_scope() as db:
                rows = (await db.scalars(select(MemoryEntry).where(or_(MemoryEntry.embedding.is_(None), MemoryEntry.embed_model != m)).limit(16))).all()
                if not rows:
                    return
                out = await embed([r.text for r in rows])
                if not out:
                    return
                for r, v in zip(rows, out[1]):
                    r.embedding, r.embed_model = to_bytes(v), out[0]


async def _later() -> None:
    await asyncio.sleep(1.5)  # let the request that added the entry commit first
    try:
        await backfill()
    except Exception:  # never let a background task crash loudly
        log.exception("embedding backfill failed")


def schedule_backfill() -> None:
    """Fire and forget (debounced by the lock)."""
    global _pending
    if _pending and not _pending.done():
        return
    try:
        _pending = asyncio.get_running_loop().create_task(_later())
    except RuntimeError:
        pass
