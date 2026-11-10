"""Interne REST-API fuer andere Dienste im IDA-Stack (Docker-Netz, NICHT nach aussen veroeffentlicht).

* GET  /api/v1/status                    Verbindung, Abgleich- und Backup-Stand
* GET  /api/v1/auth/status              Google-Anmeldung: verbunden, Konto, welche Dienste erlaubt sind
* POST /api/v1/auth/start               -> {url}: Googles Zustimmungsseite (state 10 Minuten gueltig)
* POST /api/v1/auth/callback            {code, state}: Rueckweg von Google (kommt aus IDA), speichert den Zugang
* POST /api/v1/auth/disconnect          Zugang bei Google widerrufen und loeschen
* GET  /api/v1/tools                     alle Google-Funktionen (dieselben wie im MCP) mit Beschreibung und Parametern
* POST /api/v1/tools/{name}              eine Funktion ausfuehren, Body = JSON mit den Parametern
* GET/POST /api/v1/sync/calendar         Stand / jetzt abgleichen (?dry_run=1 = nur anzeigen, was passieren wuerde)
* GET/POST /api/v1/backup/docs           Stand / Docs-Sicherung jetzt nach Drive legen
Ist GOOGLE_API_TOKEN gesetzt, ist es als Bearer-Token noetig.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.google_auth import GoogleAuthError
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


SERVICE_NAMES = {"tasks": "Aufgaben", "contacts": "Kontakte", "calendar": "Kalender", "gmail": "Gmail", "docs": "Docs", "drive": "Drive",
                 "sheets": "Sheets", "slides": "Folien", "chat": "Chat", "meet": "Meet", "health": "Gesundheit"}


def build_internal_app(settings, mcp, google, runner: Runner, services: dict | None = None) -> Starlette:
    """services: Name des Dienstes (tasks, calendar, ...) -> seine Scopes, fuer die Anzeige „was ist schon erlaubt“."""
    services = services or {}

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

    async def auth_status(request: Request):
        info = await asyncio.to_thread(google.info)
        granted = set(info.get("erteilt", []))
        dienste = [{"name": SERVICE_NAMES.get(k, k), "ok": bool(info.get("verbunden")) and set(v) <= granted} for k, v in services.items()]
        missing = [x for x in google.required_scopes() if x not in granted] if info.get("verbunden") else google.required_scopes()
        return JSONResponse({"verbunden": bool(info.get("verbunden")), "konto": info.get("konto", ""), "fehler": info.get("fehler", ""),
                             "dienste": dienste, "fehlende_scopes": len(missing), "vollstaendig": bool(info.get("verbunden")) and not missing,
                             })

    async def auth_start(request: Request):
        """Adresse von Googles Zustimmungsseite (mit frischem state). Zurueck kommt Google bei GOOGLE_REDIRECT_URI (in IDA)."""
        return JSONResponse({"url": google.build_authorize_url(google.generate_state())})

    async def auth_callback(request: Request):
        """IDA reicht code und state vom Rueckweg durch; hier wird der state geprueft und der Zugang gespeichert."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        code, state = str((body or {}).get("code") or ""), str((body or {}).get("state") or "")
        if not code or not google.consume_state(state):
            return JSONResponse({"detail": "Ungueltiger oder abgelaufener Anmelde-Versuch – bitte noch einmal starten"}, status_code=400)
        try:
            await asyncio.to_thread(google.exchange_code, code)
        except GoogleAuthError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})

    async def auth_disconnect(request: Request):
        await asyncio.to_thread(google.disconnect)
        return JSONResponse({"ok": True})

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
        Route("/api/v1/auth/status", guarded(auth_status)),
        Route("/api/v1/auth/start", guarded(auth_start), methods=["POST"]),
        Route("/api/v1/auth/callback", guarded(auth_callback), methods=["POST"]),
        Route("/api/v1/auth/disconnect", guarded(auth_disconnect), methods=["POST"]),
        Route("/api/v1/tools", guarded(tools)),
        Route("/api/v1/tools/{name}", guarded(call_tool), methods=["POST"]),
        Route("/api/v1/sync/calendar", guarded(calendar_get), methods=["GET"]),
        Route("/api/v1/sync/calendar", guarded(calendar_run), methods=["POST"]),
        Route("/api/v1/backup/docs", guarded(backup_get), methods=["GET"]),
        Route("/api/v1/backup/docs", guarded(backup_run), methods=["POST"]),
    ])
