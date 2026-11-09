"""Chat mit Ida in Docs: reicht wenige freigegebene Aufrufe an IDA weiter (Antworten gestreamt, nichts gepuffert)."""
import logging
import re
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .config import get_settings

log = logging.getLogger("docs.ida")
router = APIRouter(prefix="/api/v1/ida", include_in_schema=False)
_ID = r"[A-Za-z0-9_-]{1,64}"
# Nur das Nötigste: Chatten und Modelle ansehen. Was nicht hier steht, ist über Docs nicht erreichbar.
ALLOWED = {"GET": [re.compile(p) for p in (rf"chats/{_ID}", rf"chats/{_ID}/live")],
           "POST": [re.compile(p) for p in (r"chat", rf"chats/{_ID}/stop")]}
BOOT_KEYS = ("models", "model_names", "default_model")
BAD_GATEWAY = {"detail": "Ida ist gerade nicht erreichbar"}


def _client() -> httpx.AsyncClient:
    # lange Antworten: kein Lese-Timeout, nur beim Verbinden
    return httpx.AsyncClient(base_url=get_settings().ida_internal_url.rstrip("/"), timeout=httpx.Timeout(connect=10, read=None, write=60, pool=10))


@router.get("/bootstrap")
async def bootstrap():
    """Nur die Modell-Liste, nicht die übrigen Daten aus IDA."""
    try:
        async with _client() as c:
            r = await c.get("/api/v1/bootstrap")
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("IDA nicht erreichbar: %s", e)
        return JSONResponse(BAD_GATEWAY, status_code=502)
    return {k: data.get(k) for k in BOOT_KEYS}


@router.api_route("/{path:path}", methods=["GET", "POST"])
async def proxy(path: str, request: Request):
    if not any(p.fullmatch(path) for p in ALLOWED[request.method]):
        return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
    client = _client()
    body = await request.body() if request.method == "POST" else None
    req = client.build_request(request.method, "/api/v1/" + quote(path, safe="/"), params=request.query_params.multi_items(),
                               headers={"content-type": "application/json"}, content=body)
    try:
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        log.warning("IDA nicht erreichbar: %s", e)
        await client.aclose()
        return JSONResponse(BAD_GATEWAY, status_code=502)

    async def close() -> None:
        await resp.aclose()
        await client.aclose()

    return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, background=BackgroundTask(close),
                             headers={"cache-control": "no-cache", "x-accel-buffering": "no"}, media_type=resp.headers.get("content-type"))
