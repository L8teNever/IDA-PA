"""Web-App fuer den Auth-Port: einmaliger (oder bei neuen Scopes erneuter)
Google-Anmelde-Flow. Gedacht, um zusaetzlich hinter Cloudflare Zero Trust
Access zu liegen (siehe README) -- AUTH_TOKEN als ?token= ist nur ein
zusaetzliches, kostenloses Sicherheitsnetz, kein Ersatz dafuer.

/oauth/callback braucht keinen eigenen Token-Check: Google ruft diese URL
per Redirect selbst auf (kann also keinen eigenen Header/Query-Token
mitschicken) -- der state-Parameter (siehe app/google_auth.py) beweist
stattdessen, dass der Callback zu einem hier selbst gestarteten /authorize-
Aufruf gehoert.
"""

from __future__ import annotations

import asyncio
import hmac
import html
import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from app.config import Settings
from app.google_auth import GoogleAuthError, GoogleAuthManager

log = logging.getLogger("ida-google.auth")


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='de'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title>"
            "<style>body{font:16px system-ui;background:#000;color:#e6e6e6;max-width:520px;margin:12vh auto;padding:0 20px}h1{font-size:22px}"
            ".w{color:#f2b84b}.b{display:inline-block;background:#7fd3ff;color:#00344d;padding:10px 18px;border-radius:20px;text-decoration:none;font-weight:600}</style>"
            f"<body>{body}</body></html>")


def build_auth_app(settings: Settings, google: GoogleAuthManager) -> Starlette:
    def _token_ok(request: Request) -> bool:
        provided = request.query_params.get("token", "")
        return bool(provided) and hmac.compare_digest(provided, settings.auth_token)

    async def healthz(request: Request):
        return JSONResponse({"status": "ok"})

    async def status(request: Request):
        if not _token_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"verbunden": google.is_connected()})

    async def home(request: Request):
        """Startseite der Anmeldeadresse: zeigt nur den Stand. Anmelden geht aus IDA (Einstellungen -> Google) oder mit ?token=."""
        info = await asyncio.to_thread(google.info)
        missing = [s for s in google.required_scopes() if s not in info.get("erteilt", [])]
        start = f'<p><a class="b" href="/authorize?token={html.escape(request.query_params.get("token", ""))}">Mit Google anmelden</a></p>' if _token_ok(request) else \
            "<p>Anmelden: in IDA unter <b>Einstellungen → Google</b>.</p>"
        body = (f"<h1>Google: {'verbunden' if info.get('verbunden') else 'nicht verbunden'}</h1>"
                + (f"<p>Konto: {html.escape(info['konto'])}</p>" if info.get("konto") else "")
                + (f"<p class='w'>Fehlende Berechtigungen: {len(missing)} – bitte neu anmelden.</p>" if info.get("verbunden") and missing else "")
                + (f"<p class='w'>{html.escape(info['fehler'])}</p>" if info.get("fehler") else "") + start)
        return HTMLResponse(_page("IDA Google", body))

    async def authorize(request: Request):
        back = ""
        ticket = request.query_params.get("ticket", "")
        if ticket:
            ok, back = google.use_ticket(ticket)
            if not ok:
                return HTMLResponse(_page("Abgelaufen", "<h1>Link abgelaufen</h1><p>Bitte in IDA unter Einstellungen → Google noch einmal auf „Anmelden“ tippen.</p>"), status_code=400)
        elif not _token_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        state = google.generate_state(back)
        return RedirectResponse(google.build_authorize_url(state))

    async def oauth_callback(request: Request):
        error = request.query_params.get("error")
        if error:
            return HTMLResponse(_page("Abgebrochen", f"<h1>Google-Anmeldung abgebrochen</h1><p>{html.escape(error)}</p>"), status_code=400)

        code = request.query_params.get("code")
        state = request.query_params.get("state", "")
        valid, back = google.consume(state)
        if not code or not valid:
            return HTMLResponse(
                "<h1>Ungueltiger oder abgelaufener Anmelde-Versuch</h1>"
                "<p>Bitte /authorize erneut aufrufen.</p>",
                status_code=400,
            )

        try:
            google.exchange_code(code)
        except GoogleAuthError as exc:
            log.exception("Google-Code-Tausch fehlgeschlagen")
            return HTMLResponse(f"<h1>Fehler</h1><p>{exc}</p>", status_code=500)

        if back:   # zurueck zu IDA (die Adresse wurde beim Start geprueft)
            return RedirectResponse(back + ("&" if "?" in back else "?") + "google=ok")
        return HTMLResponse(_page("Verbunden", "<h1>Erfolgreich mit Google verbunden</h1><p>Dieses Fenster kann geschlossen werden.</p>"))

    return Starlette(
        routes=[
            Route("/", home, methods=["GET"]),
            Route("/healthz", healthz, methods=["GET"]),
            Route("/status", status, methods=["GET"]),
            Route("/authorize", authorize, methods=["GET"]),
            Route("/oauth/callback", oauth_callback, methods=["GET"]),
        ]
    )
