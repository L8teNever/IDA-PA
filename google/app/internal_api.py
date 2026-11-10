"""Interne REST-API fuer andere Dienste im IDA-Stack (Docker-Netz, NICHT nach aussen veroeffentlicht).

* GET  /api/v1/status                    Verbindung, Abgleich- und Backup-Stand
* GET  /api/v1/tools                     alle Google-Funktionen (dieselben wie im MCP) mit Beschreibung und Parametern
* POST /api/v1/tools/{name}              eine Funktion ausfuehren, Body = JSON mit den Parametern
* GET/POST /api/v1/sync/calendar         Stand / jetzt abgleichen (?dry_run=1 = nur anzeigen, was passieren wuerde)
* GET/POST /api/v1/backup/docs           Stand / Docs-Sicherung jetzt nach Drive legen
Ist GOOGLE_API_TOKEN gesetzt, ist es als Bearer-Token noetig.
"""
from __future__ import annotations

import hmac
import json
import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.google_client import GoogleApiError
from app.jobs.runner import Runner

log = logging.getLogger("ida-google.api")


def _plain(result):
    """FastMCP liefert je nach Version Inhalte, (Inhalte, strukturiert) oder ein Dict -> schlichtes JSON."""
    if isinstance(result, tuple) and len(result) == 2:
        result = result[1]
    if isinstance(result, dict):
        return result.get("result", result) if set(result) == {"result"} else result
    if isinstance(result, (list, tuple)):
        out = []
        for c in result:
            t = getattr(c, "text", None)
            if t is None:
                out.append(c)
                continue
            try:
                out.append(json.loads(t))
            except ValueError:
                out.append(t)
        return out[0] if len(out) == 1 else out
    return result


def build_internal_app(settings, mcp, google, runner: Runner) -> Starlette:
    def _deny(request: Request) -> JSONResponse | None:
        if not settings.api_token:
            return None
        h = request.headers.get("authorization", "")
        tok = h[7:].strip() if h.lower().startswith("bearer ") else ""
        return None if tok and hmac.compare_digest(tok, settings.api_token) else JSONResponse({"detail": "Nicht erlaubt"}, status_code=401)

    def guarded(fn):
        async def wrapper(request: Request):
            return _deny(request) or await fn(request)
        return wrapper

    async def healthz(request: Request):
        return JSONResponse({"status": "ok"})

    async def status(request: Request):
        return JSONResponse({"verbunden": google.is_connected(), "kalender_abgleich": runner.calendar_status(), "docs_backup": runner.backup_status()})

    async def tools(request: Request):
        return JSONResponse([{"name": t.name, "beschreibung": t.description, "parameter": t.inputSchema} for t in await mcp.list_tools()])

    async def call_tool(request: Request):
        name = request.path_params["name"]
        try:
            args = await request.json() if await request.body() else {}
        except ValueError:
            return JSONResponse({"detail": "Body ist kein JSON"}, status_code=400)
        if not isinstance(args, dict):
            return JSONResponse({"detail": "Body muss ein JSON-Objekt mit den Parametern sein"}, status_code=400)
        if name not in {t.name for t in await mcp.list_tools()}:
            return JSONResponse({"detail": f"Funktion {name} gibt es nicht"}, status_code=404)
        try:
            return JSONResponse({"ergebnis": _plain(await mcp.call_tool(name, args))}, media_type="application/json")
        except GoogleApiError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=502)
        except Exception as exc:  # noqa: BLE001 – FastMCP verpackt Fehler (falsche Parameter, Google-Fehler) selbst
            log.info("Funktion %s: %s", name, exc)
            return JSONResponse({"detail": str(exc)[:500]}, status_code=400)

    async def calendar_get(request: Request):
        return JSONResponse(runner.calendar_status())

    async def calendar_run(request: Request):
        dry = request.query_params.get("dry_run", "").lower() in ("1", "true", "ja")
        return JSONResponse(await runner.run_calendar(dry_run=dry))

    async def backup_get(request: Request):
        return JSONResponse(runner.backup_status())

    async def backup_run(request: Request):
        return JSONResponse(await runner.run_backup())

    return Starlette(routes=[
        Route("/healthz", healthz),
        Route("/api/v1/status", guarded(status)),
        Route("/api/v1/tools", guarded(tools)),
        Route("/api/v1/tools/{name}", guarded(call_tool), methods=["POST"]),
        Route("/api/v1/sync/calendar", guarded(calendar_get), methods=["GET"]),
        Route("/api/v1/sync/calendar", guarded(calendar_run), methods=["POST"]),
        Route("/api/v1/backup/docs", guarded(backup_get), methods=["GET"]),
        Route("/api/v1/backup/docs", guarded(backup_run), methods=["POST"]),
    ])
