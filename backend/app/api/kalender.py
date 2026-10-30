"""Kalender-Dienst ↔ IDA: Webhook für fällige Erinnerungen und der Proxy /kal/* (damit die Kalender-Oberfläche in IDA unter einem Origin läuft)."""
import hmac
from datetime import datetime

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import Agent
from ..services import kalender_client as kc
from ..services import reminders
from ..services.schedule import tz

router = APIRouter(tags=["kalender"], include_in_schema=False)
_WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authenticate", "proxy-authorization", "content-length"}


# ---------------- Webhook: eine Erinnerung ist fällig ----------------
def _in(minutes: int) -> str:
    if minutes <= 0:
        return "jetzt"
    if minutes < 60:
        return f"in {minutes} Min."
    if minutes % 1440 == 0:
        d = minutes // 1440
        return "morgen" if d == 1 else f"in {d} Tagen"
    if minutes % 60 == 0:
        return f"in {minutes // 60} Std."
    return f"in {minutes // 60} Std. {minutes % 60} Min."


def alarm_text(p: dict) -> str:
    """„Do, 30.10. 15:00 · Praxis Müller (in 30 Min.)“"""
    if p.get("all_day"):
        when = "ganztägig"
    else:
        s = datetime.fromisoformat(p["start"])
        now = datetime.now(tz())
        when = f"{s:%H:%M}" if s.date() == now.date() else f"{_WD[s.weekday()]} {s:%d.%m. %H:%M}"
    parts = [when] + ([p["location"]] if p.get("location") else [])
    return " · ".join(parts) + f" ({_in(int(p.get('minutes') or 0))})"


def _authorized(request: Request) -> bool:
    tok = get_settings().kalender_ida_token
    h = request.headers.get("authorization", "")
    got = h[7:].strip() if h.lower().startswith("bearer ") else ""
    return bool(tok) and hmac.compare_digest(got.encode(), tok.encode())


@router.post("/api/v1/ext/kalender")
async def webhook(request: Request):
    if not _authorized(request):
        return JSONResponse({"detail": "Nicht erlaubt"}, status_code=401)
    p = await request.json()
    if p.get("type") != "alarm":
        return JSONResponse({"ok": True, "ignored": True})
    async with session_scope() as db:
        main = await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
    if not main:
        return JSONResponse({"detail": "Kein Haupt-Agent"}, status_code=409)
    title = str(p.get("title") or "Termin")
    if p.get("action") == "agent" and p.get("task"):
        # der Agent erledigt die Aufgabe jetzt – über die vorhandene Erinnerungs-/Job-Strecke, das Ergebnis kommt als Benachrichtigung
        async with session_scope() as db:
            await reminders.create(db, {"text": f"Zum Termin „{title}“ ({alarm_text(p)}) sollst du: {p['task']}", "kind": "task", "agent_id": main.id,
                                        "when": datetime.now(tz()).strftime("%Y-%m-%dT%H:%M"), "show_on_home": False}, created_by="kalender")
    else:
        await reminders._notify(main.id, f"📅 {title}", alarm_text(p))  # noqa: SLF001 – zählt nicht gegen das Nachrichten-Limit
    kc.invalidate()
    return {"ok": True}


# ---------------- „Ida fragen“ aus dem Kalender ----------------
@router.post("/api/v1/ext/kalender/ask")
async def ask(request: Request):
    """Der Kalender reicht eine Frage weiter; der Haupt-Agent beantwortet sie (mit den calendar_*-Tools) und die Antwort geht zurück."""
    if not _authorized(request):
        return JSONResponse({"detail": "Nicht erlaubt"}, status_code=401)
    p = await request.json()
    text = str(p.get("text") or "").strip()[:2000]
    if not text:
        return JSONResponse({"detail": "Frage fehlt"}, status_code=400)
    async with session_scope() as db:
        main = await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
    if not main:
        return JSONResponse({"detail": "Kein Haupt-Agent"}, status_code=409)
    from ..services import runs
    ctx = str(p.get("context") or "")[:500]
    prompt = (f"Der Nutzer fragt dich aus seinem IDA Kalender heraus: {text}\n{ctx}\n"
              "Nutze die calendar_*-Tools (lesen, anlegen, ändern), wenn es um Termine geht. Antworte kurz und direkt auf Deutsch.")
    r = await runs.run_agent(main.id, prompt)
    kc.invalidate()
    return {"ok": r.ok, "text": r.text or ("Ich konnte gerade nicht antworten." if not r.ok else "")}


# ---------------- Proxy ----------------
@router.api_route("/kal/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    """Reicht alles unter /kal/ an den Kalender-Dienst weiter (auch den Live-Stream); setzt das IDA-Token, der Absender ist der Nutzer."""
    base = get_settings().kalender_url.rstrip("/")
    if not base:
        return JSONResponse({"detail": "Der Kalender ist nicht eingerichtet (KALENDER_URL fehlt)"}, status_code=503)
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept", "accept-encoding", "last-event-id", "if-none-match", "range")}
    headers["x-forwarded-prefix"] = "/kal"
    headers.update(kc._headers("user"))  # noqa: SLF001
    client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=None))
    try:
        req = client.build_request(request.method, f"{base}/{path}", params=request.query_params, headers=headers, content=await request.body())
        r = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"detail": "Kalender-Dienst nicht erreichbar"}, status_code=503)

    async def body():
        try:
            async for chunk in r.aiter_raw():
                yield chunk
        finally:
            await r.aclose()
            await client.aclose()

    out = {k: v for k, v in r.headers.items() if k.lower() not in HOP}
    out["x-accel-buffering"] = "no"
    return StreamingResponse(body(), status_code=r.status_code, headers=out)
