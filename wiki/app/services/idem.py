"""Idempotenz für Schreibaufrufe (`Idempotency-Key` der Offline-Outbox)."""
from datetime import timedelta

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete

from ..db import pending, utcnow
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..models import RequestRec

KEEP = timedelta(days=14)
_CODES = ((Conflict, 409), (Forbidden, 403), (NotFound, 404), (Invalid, 400))


def _key(request: Request) -> str | None:
    k = (request.headers.get("idempotency-key") or "").strip()
    return k[:64] if k else None


async def run(db, request: Request, fn):
    key = _key(request)
    if not key:
        return await fn()
    path = request.url.path[:200]
    row = await db.get(RequestRec, key)
    if row:
        if row.method != request.method or row.path != path:
            return JSONResponse({"detail": "Dieser Idempotency-Key gehört zu einer anderen Anfrage"}, status_code=422)
        return JSONResponse(row.response, status_code=row.status, headers={"Idempotent-Replay": "1"})
    marker = len(pending(db))
    try:
        async with db.begin_nested():
            payload = await fn()
        status = 200
    except tuple(e for e, _ in _CODES) as e:
        del pending(db)[marker:]
        status = next(c for t, c in _CODES if isinstance(e, t))
        payload = {"detail": str(e), **getattr(e, "extra", {})}
        db.add(RequestRec(key=key, method=request.method, path=path, status=status, response=payload))
        return JSONResponse(payload, status_code=status)
    db.add(RequestRec(key=key, method=request.method, path=path, status=status, response=payload))
    return payload


async def purge(db) -> int:
    r = await db.execute(delete(RequestRec).where(RequestRec.at < utcnow() - KEEP))
    return r.rowcount or 0
