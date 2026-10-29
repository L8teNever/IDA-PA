"""Health check and the data every page needs at start (agents, models, accounts, counters)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, UploadFile
from sqlalchemy import func, select

from ..config import get_settings
from ..db import utcnow
from ..models import USER, Account, Agent, Connector, Message, Reminder, Routine, RoutineRun, Skill, Tracker
from ..schemas import HomeCalendar
from ..services import activity, home_calendar, jobs, kalender_client, providers, settings_store, speech, trackers
from ..services import code as code_area
from ..services.agents import PALETTES
from ..services.claude.usage import logged_in
from ..services.errors import Invalid, NotFound
from ..services.mcp import client as mcp_client
from ..services.schedule import tz
from . import serializers as ser
from .deps import DB

router = APIRouter(tags=["system"])


@router.get("/api/v1/health")
async def health():
    return {"ok": True}


@router.post("/api/v1/transcribe")
async def transcribe(audio: UploadFile):
    """Microphone input: recording -> text (local Whisper). The text goes into the input field, not straight to a chat."""
    name = (audio.filename or "").lower()
    suffix = next((x for x in (".webm", ".ogg", ".mp4", ".m4a", ".wav", ".mp3") if name.endswith(x)), ".webm")
    return await speech.transcribe(await audio.read(), suffix)


@router.get("/api/v1/activity")
async def current_activity():
    """What the agents are doing right now (polled by the browser for the mascot)."""
    return {**activity.snapshot(), "jobs": jobs.status()}


@router.get("/api/v1/bootstrap")
async def bootstrap(db: DB):
    s = get_settings()
    agents = (await db.scalars(select(Agent).order_by(Agent.position))).all()
    accounts = (await db.scalars(select(Account).order_by(Account.position, Account.id))).all()
    skills = (await db.scalars(select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.name))).all()
    unread_user = await db.scalar(select(func.count()).select_from(Message).where(Message.to_id == USER, Message.read_at.is_(None)))
    return {
        "models": await providers.model_list(db), "model_names": await providers.model_names(db), "default_model": s.default_model, "palettes": list(PALETTES),
        "agents": [ser.agent(a) for a in agents],
        "accounts": [ser.account(a, providers.logged_in(a)) for a in accounts],
        "skills": [{"id": k.id, "name": k.name, "icon": k.icon, "description": k.description} for k in skills],
        "unread": unread_user or 0, "jobs": jobs.status(), "code_enabled": await code_area.enabled(db),
    }


@router.get("/api/v1/home")
async def home(db: DB):
    """Start page: unread notifications (Infos) and today's day plan – calendar events plus the routines/reminders marked for it."""
    now = utcnow()
    day = datetime.now(tz()).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = day.astimezone(timezone.utc), (day + timedelta(days=1)).astimezone(timezone.utc)  # local days: 23 or 25 h on DST changes
    notices = (await db.scalars(select(Message).where(Message.to_id == USER, Message.read_at.is_(None)).order_by(Message.priority.desc(), Message.at.desc()).limit(20))).all()
    routines = {r.id: r for r in (await db.scalars(select(Routine).where(Routine.show_on_home.is_(True)))).all()}
    runs = (await db.scalars(select(RoutineRun).where(RoutineRun.at >= start, RoutineRun.routine_id.in_(list(routines))).order_by(RoutineRun.at))).all()
    timeline = [{"kind": "run", "at": ser.iso(x.at), "title": routines[x.routine_id].name, "icon": routines[x.routine_id].icon, "ok": x.ok,
                 "agent_id": routines[x.routine_id].agent_id, "routine_id": x.routine_id, "text": x.text[:300]} for x in runs]
    for r in routines.values():
        nxt = r.next_run.replace(tzinfo=now.tzinfo) if r.next_run and r.next_run.tzinfo is None else r.next_run
        if r.enabled and nxt and nxt < end:
            timeline.append({"kind": "next", "at": ser.iso(nxt), "title": r.name, "icon": r.icon, "agent_id": r.agent_id, "routine_id": r.id})
    for r in (await db.scalars(select(Reminder).where(Reminder.due_at >= start, Reminder.due_at < end, Reminder.show_on_home.is_(True)))).all():
        timeline.append({"kind": "reminder", "at": ser.iso(r.due_at), "title": ("⏰ " if r.kind == "remind" else "") + r.text[:80],
                         "agent_id": r.agent_id, "status": r.status, "task": r.kind == "task", "reminder_id": r.id})
    marked = (await db.scalars(select(Tracker).where(Tracker.show_on_home.is_(True), Tracker.enabled.is_(True)).order_by(Tracker.position))).all()
    status = await trackers.overview(db, list(marked), 1)
    for t in marked:
        today = status[t.id]["today"]
        if not today["due"]:
            continue
        for x in today["slots"]:  # without a time: on top like all-day events
            at = day.replace(hour=int(x["time"][:2]), minute=int(x["time"][3:])) if x["time"] else day
            timeline.append({"kind": "tracker", "at": ser.iso(at.astimezone(timezone.utc)), "all_day": not x["time"], "icon": t.icon,
                             "title": t.name + (f" · {x['label']}" if len(today["slots"]) > 1 else ""),
                             "tracker_id": t.id, "slot": x["key"], "done": x["done"], "by": x["by"], "duration_s": t.duration_s})
    cal, cal_error = await home_calendar.config(db), None
    if cal:
        got = await home_calendar.fetch(db, cal)
        timeline += got["events"]
        cal_error = got["error"]
    timeline.sort(key=lambda x: (not x.get("all_day"), datetime.fromisoformat(x["at"])))
    return {"notices": [ser.message(m) for m in notices], "timeline": timeline,
            "calendar": {"on": bool(cal), "error": cal_error, "local": bool(cal and cal.get("source") == "kalender")}}


@router.get("/api/v1/home-calendar")
async def calendar_settings(db: DB):
    conns = (await db.scalars(select(Connector).order_by(Connector.name))).all()
    return {"saved": await settings_store.get(db, home_calendar.KEY), "active": await home_calendar.config(db),
            "candidates": home_calendar.candidates(list(conns)), "local": kalender_client.enabled()}


@router.put("/api/v1/home-calendar")
async def save_calendar(body: HomeCalendar, db: DB):
    """off = no calendar; without connector/tool = automatic; otherwise exactly this tool."""
    if body.off:
        value = {"off": True}
    elif body.source == "kalender":
        if not kalender_client.enabled():
            raise Invalid("Der Kalender-Dienst ist nicht eingerichtet (KALENDER_URL)")
        value = {"source": "kalender"}
    elif body.connector and body.tool:
        if not await db.get(Connector, body.connector):
            raise Invalid("MCP-Server nicht gefunden")
        value = {"connector": body.connector, "tool": body.tool, "args": body.args}
    else:
        value = None
    await settings_store.put(db, home_calendar.KEY, value)
    home_calendar.clear_cache()
    return await calendar_settings(db)


@router.post("/api/v1/home-calendar/test")
async def test_calendar(body: HomeCalendar, db: DB):
    """Calls the tool once (not cached) and shows what the day plan would get."""
    cfg = dict(home_calendar.LOCAL) if body.source == "kalender" else         ({"connector": body.connector, "tool": body.tool, "args": body.args} if body.connector and body.tool else await home_calendar.config(db))
    if not cfg:
        raise Invalid("Kein Kalender-Tool gefunden – wähle einen MCP-Server und ein Tool")
    return {**await home_calendar.fetch(db, cfg, use_cache=False), "tool": cfg.get("tool") or "IDA Kalender", "connector": cfg.get("connector")}


@router.get("/api/v1/home-calendar/suggest")
async def suggest_calendar_args(connector: str, tool: str, db: DB):
    """Date arguments for a tool, guessed from its input schema (von/bis, start/end, date, month …)."""
    c = await db.get(Connector, connector)
    if not c:
        raise NotFound("MCP-Server nicht gefunden")
    try:
        schema = next((t.get("inputSchema") for t in await mcp_client.tool_defs(c) if t.get("name") == tool), None)
    except Exception as e:  # noqa: BLE001
        raise Invalid(f"MCP-Server nicht erreichbar: {str(e)[:120]}") from e
    return {"args": home_calendar.suggest_args(schema), "schema": schema}
