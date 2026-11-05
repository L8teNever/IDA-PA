"""Öffentlicher Dienst „links“ (APP_MODE=links): leitet Kurzlinks weiter – sonst nichts.

Läuft als eigener Container ohne Docker-Socket und ohne Cloudflare-Token; er kennt nur die Link-Datenbank (LINKS_DB).
Verwaltet werden die Links im IDA Server (Tool „Weiterleitungen“).

* GET /<kürzel>  → prüft die Regeln, zählt und leitet weiter (302, nie zwischengespeichert)
* Vorschau-Bots (WhatsApp, Telegram, iMessage, Slack …) und HEAD bekommen nur eine neutrale Seite und zählen NICHT – sonst wäre
  ein Einmallink schon verbraucht, bevor jemand draufklickt. Das Ziel verrät die Seite nicht.
* Links mit „Bestätigen“: erst eine Seite mit „Weiter“ (Formular per POST – Mail-Scanner öffnen nur per GET).
* Gerät = Cookie ida_dev (400 Tage). Gesperrt / abgelaufen: eigene Seite mit Grund, ohne das Ziel.
* Jedes Kürzel gilt auf allen LINKS_DOMAINS. Ein Cookie gilt aber nur je Domain: Links mit Geräte-Regel springen deshalb erst auf
  ihre Stamm-Domain, sonst wäre dasselbe Handy auf l.… und link.… zwei Geräte.
* Durchprobieren: zu viele unbekannte Kürzel von einer IP → Pause (Guard).
"""
import html
import re
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from .config import get_settings
from .links_store import REASONS, Store, device_id

COOKIE = "ida_dev"
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BOTS = re.compile(r"bot|crawl|spider|preview|facebookexternalhit|whatsapp|telegram|slack|discord|twitter|skype|linkedin|embedly|"
                  r"pinterest|vkshare|quora|redditbot|applebot|google-?(read|inspection)|bingpreview|outlook|microsoft office|"
                  r"curl|wget|python-requests|httpx|go-http|okhttp|headless", re.I)
NO_STORE = {"cache-control": "no-store, private", "x-robots-tag": "noindex, nofollow", "referrer-policy": "no-referrer"}


class Guard:
    """Bremst Durchprobieren: höchstens `limit` unbekannte Kürzel je IP in `window` Sekunden, danach Pause (auch für echte Kürzel)."""

    def __init__(self, limit: int = 20, window: int = 600):
        self.limit, self.window = limit, window
        self.misses: dict[str, deque] = {}

    def _fresh(self, ip: str, now: float) -> deque:
        q = self.misses.get(ip, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def blocked(self, ip: str, now: float | None = None) -> bool:
        return len(self._fresh(ip, now or time.time())) >= self.limit

    def miss(self, ip: str, now: float | None = None) -> None:
        now = now or time.time()
        if len(self.misses) > 5000:  # Speicher begrenzen: abgelaufene IPs raus
            self.misses = {k: q for k, q in self.misses.items() if self._fresh(k, now)}
        self.misses.setdefault(ip, deque()).append(now)


def page(title: str, text: str, *, status: int = 200, body: str = "", icon: str = "link") -> HTMLResponse:
    t, x = html.escape(title), html.escape(text)
    icons = {"link": "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1m2 5a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
             "lock": "M7 11V8a5 5 0 0 1 10 0v3M6 11h12v10H6z", "time": "M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z"}
    return HTMLResponse(f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta property="og:title" content="{t}"><meta name="color-scheme" content="dark"><title>{t}</title>
<style>body{{margin:0;min-height:100dvh;display:grid;place-items:center;background:#000;color:#e3e3e3;font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:420px;margin:24px;padding:32px 28px;border-radius:28px;background:#121316;text-align:center}}
svg{{width:44px;height:44px;stroke:#a8c7fa;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;margin-bottom:12px}}
h1{{font-size:21px;font-weight:500;margin:0 0 8px}}p{{color:#9aa0a6;margin:0}}
button,a.btn{{margin-top:22px;border:0;border-radius:999px;background:#a8c7fa;color:#062e6f;font:inherit;font-weight:500;padding:12px 28px;cursor:pointer;text-decoration:none;display:inline-block}}</style></head>
<body><main><svg viewBox="0 0 24 24"><path d="{icons.get(icon, icons['link'])}"/></svg><h1>{t}</h1><p>{x}</p>{body}</main></body></html>""",
                        status_code=status, headers=NO_STORE)


def create_links_app() -> FastAPI:
    s = get_settings()
    store = Store(s.links_db)
    app = FastAPI(title="IDA Links", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.links = store
    app.state.guard = guard = Guard()
    domains = s.link_domains

    @app.get("/api/v1/health", include_in_schema=False)
    async def health():
        return {"ok": True}

    @app.get("/robots.txt", include_in_schema=False)
    async def robots():
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=404)

    @app.get("/", include_in_schema=False)
    async def root():
        return page("Weiterleitung", "Hier gibt es nichts zu sehen – nur Kurzlinks.")

    def device_of(req: Request) -> tuple[str, bool]:
        d = req.cookies.get(COOKIE, "")
        return (d, False) if re.match(r"^[A-Za-z0-9_-]{16,64}$", d) else (device_id(), True)

    def with_cookie(resp: Response, dev: str, new: bool) -> Response:
        if new:
            resp.set_cookie(COOKIE, dev, max_age=400 * 86400, secure=True, httponly=True, samesite="lax")
        return resp

    def home_of(link: dict, host: str) -> str:
        """Stamm-Domain, wenn der Link eine Geräte-Regel hat und gerade über eine andere seiner Domains aufgerufen wird; sonst leer."""
        if link["device_mode"] == "any" or host not in domains:
            return ""
        home = link["domain"] if link["domain"] in domains else domains[0]
        return "" if host == home else home

    async def go(req: Request, code: str, confirmed: bool) -> Response:
        ip = req.headers.get("cf-connecting-ip") or (req.client.host if req.client else "")
        if guard.blocked(ip):
            return page("Zu viele Versuche", "Bitte versuch es später nochmal.", status=429, icon="time")
        link = store.by_code(code) if CODE_RE.match(code) else None
        if not link:
            guard.miss(ip)
            return page("Link nicht gefunden", "Diesen Link gibt es nicht (mehr).", status=404)
        agent = req.headers.get("user-agent", "")
        if BOTS.search(agent) or req.method == "HEAD":  # Vorschau: neutral, zählt nicht
            return page("Weiterleitung", "Öffne den Link im Browser, um weiterzukommen.")
        home = home_of(link, req.headers.get("host", "").split(":")[0].lower())
        if home:
            return RedirectResponse(f"https://{home}/{link['code']}", status_code=307, headers=NO_STORE)
        dev, new = device_of(req)
        if link["confirm"] and not confirmed:
            form = f'<form method="post" action="/{html.escape(code)}"><button type="submit">Weiter</button></form>'
            return with_cookie(page("Weiterleitung", "Tippe auf „Weiter“, um den Link zu öffnen.", body=form), dev, new)
        link, reason = store.open(code, dev, country=req.headers.get("cf-ipcountry", ""), agent=agent)
        if reason:
            icon = "time" if reason in ("expired", "not_yet") else "lock"
            return with_cookie(page("Link nicht verfügbar", REASONS.get(reason, "Dieser Link ist nicht verfügbar."), status=410 if reason in ("expired", "used_up") else 403, icon=icon), dev, new)
        return with_cookie(RedirectResponse(link["target"], status_code=302, headers=NO_STORE), dev, new)

    @app.api_route("/{code}", methods=["GET", "HEAD"], include_in_schema=False)
    async def open_get(code: str, req: Request):
        return await go(req, code, False)

    @app.post("/{code}", include_in_schema=False)
    async def open_post(code: str, req: Request):
        return await go(req, code, True)

    return app
