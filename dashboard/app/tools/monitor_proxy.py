"""Nur IDA Server: Messwerte (CPU/RAM/Netz je Container, Verlauf) kommen vom Dashboard, statt doppelt zu messen.

Die Stacks-Ansicht ist im Server dieselbe wie im Dashboard; ihre Aufrufe unter /api/v1/monitor/* reicht der Server intern an
DASHBOARD_URL weiter. Ohne Dashboard fehlen nur die Zahlen, alles andere geht.
"""
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..config import get_settings
from . import Tool

router = APIRouter()


@router.get("/api/v1/monitor/{path:path}")
async def proxy(path: str, req: Request):
    base = get_settings().dashboard_url.strip().rstrip("/")
    if not base:
        return JSONResponse({"detail": "Kein Dashboard eingerichtet (DASHBOARD_URL) – Messwerte gibt es nur dort"}, status_code=503)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{base}/api/v1/monitor/{path}", params=dict(req.query_params))
    except httpx.HTTPError:
        return JSONResponse({"detail": "Dashboard nicht erreichbar"}, status_code=503)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


tool = Tool(id="monitor", name="Messwerte (vom Dashboard)", icon="monitoring", router=router)
