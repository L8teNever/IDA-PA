"""Idempotenz für Schreibaufrufe: mit dem Header `Idempotency-Key` wird dieselbe Anfrage nur einmal ausgeführt.

Der Offline-Abgleich der App sendet jede wartende Änderung mit einem festen Schlüssel. Reißt die Verbindung ab, nachdem der Server
schon gespeichert hat, schickt die App sie erneut – und bekommt dieselbe Antwort, ohne dass etwas doppelt entsteht.
Die Antwort wird in DERSELBEN Transaktion wie die Änderung gespeichert (beides oder nichts). Fehlerantworten (4xx) werden ebenfalls
gemerkt, 5xx nicht.
"""
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
    """`fn()` liefert die Antwort (dict). Ohne Schlüssel läuft sie wie gewohnt."""
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
        async with db.begin_nested():            # bei einem Fehler wird nur diese Anfrage zurückgenommen, die Antwort darunter bleibt
            payload = await fn()
        status = 200
    except tuple(e for e, _ in _CODES) as e:
        del pending(db)[marker:]                  # nichts ankündigen, was nicht passiert ist
        status = next(c for t, c in _CODES if isinstance(e, t))
        payload = {"detail": str(e), **getattr(e, "extra", {})}
        db.add(RequestRec(key=key, method=request.method, path=path, status=status, response=payload))
        return JSONResponse(payload, status_code=status)
    db.add(RequestRec(key=key, method=request.method, path=path, status=status, response=payload))
    return payload


async def purge(db) -> int:
    r = await db.execute(delete(RequestRec).where(RequestRec.at < utcnow() - KEEP))
    return r.rowcount or 0
