"""Routines: validation, CRUD, runs (schedule / trigger / manual) and trigger checks."""
import asyncio
import json
import logging
import time
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import Agent, Connector, Routine, RoutineRun
from . import jobs, triggers
from .agents import record_run
from .errors import Invalid, NotFound
from .mcp.client import McpError, call_tool

log = logging.getLogger("ida.routines")
MAX_ROUTINES = 50
MAX_HISTORY = 15
MAX_INTERVAL = 60 * 24 * 30
_running: set[str] = set()
_checking: set[str] = set()


async def validate_trigger(db: AsyncSession, t: dict | None) -> dict | None:
    """Only the user sets triggers (they call connector tools automatically)."""
    if not t or t.get("type") in ("interval", "schedule") or (t.get("type") is None and not t.get("connector")):
        return None
    if t.get("type", "mcp") != "mcp":
        raise Invalid("trigger.type: mcp")
    every = int(t.get("every_sec") or 300)
    if not 10 <= every <= 86400:
        raise Invalid("Prüf-Intervall: 10 Sekunden bis 24 Stunden")
    mode = t.get("mode") or "new"
    if mode not in ("new", "change", "contains"):
        raise Invalid("Bedingung: new, change oder contains")
    contains = str(t.get("contains") or "").strip()
    if mode == "contains" and not 0 < len(contains) <= 200:
        raise Invalid("Bei „enthält Text“ muss der Text (max. 200 Zeichen) angegeben werden")
    args = t.get("args") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError as e:
            raise Invalid("Argumente sind kein gültiges JSON") from e
    if not isinstance(args, dict) or len(json.dumps(args)) > 2000:
        raise Invalid("Argumente: JSON-Objekt, höchstens 2000 Zeichen")
    c = await db.get(Connector, str(t.get("connector") or ""))
    if not c:
        raise Invalid("Connector nicht gefunden (Einstellungen → MCP-Server)")
    if not c.tools:
        raise Invalid("Die Tool-Liste dieses Connectors ist nicht geladen – erst testen/aktualisieren")
    if t.get("tool") not in c.tools:
        raise Invalid(f"Tool „{t.get('tool')}“ gibt es bei {c.name} nicht")
    return {"type": "mcp", "connector": c.id, "tool": t["tool"], "args": args, "every_sec": every, "mode": mode,
            "contains": contains if mode == "contains" else "", "state": {}}


async def _clean(db: AsyncSession, f: dict, partial: bool, allow_trigger: bool) -> dict:
    s = get_settings()
    v: dict = {}
    if not partial or "name" in f:
        n = str(f.get("name") or "").strip()
        if not n or len(n) > 60:
            raise Invalid("Name: 1–60 Zeichen")
        v["name"] = n
    if not partial or "prompt" in f:
        p = str(f.get("prompt") or "").strip()
        if not p or len(p) > 4000:
            raise Invalid("Aufgabe: 1–4000 Zeichen")
        v["prompt"] = p
    if "agent_id" in f or not partial:
        aid = f.get("agent_id")
        agent = await db.get(Agent, aid) if aid else await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
        if not agent:
            raise Invalid("Agent nicht gefunden")
        v["agent_id"] = agent.id
    if "model" in f:
        if f["model"] and f["model"] not in s.model_list:
            raise Invalid(f"model muss eines von {', '.join(s.model_list)} sein")
        v["model"] = f["model"] or None
    if "account_id" in f:
        v["account_id"] = f["account_id"] or None
    if f.get("icon"):
        v["icon"] = str(f["icon"])[:40]
    for b in ("fallback", "memory_enabled"):
        if b in f:
            v[b] = None if f[b] is None else bool(f[b])
    if f.get("enabled") is not None:
        v["enabled"] = bool(f["enabled"])
    if allow_trigger and "trigger" in f:
        v["trigger"] = await validate_trigger(db, f["trigger"])
    if "interval_min" in f or (not partial and not v.get("trigger")):
        n = f.get("interval_min")
        if n is None and v.get("trigger"):
            n = max(1, v["trigger"]["every_sec"] // 60)
        if not isinstance(n, int) or not 1 <= n <= MAX_INTERVAL:
            raise Invalid(f"Intervall: ganze Zahl 1–{MAX_INTERVAL} (Minuten)")
        v["interval_min"] = n
    return v


async def create(db: AsyncSession, f: dict, *, allow_trigger: bool = False) -> Routine:
    if await db.scalar(select(func.count()).select_from(Routine)) >= MAX_ROUTINES:
        raise Invalid(f"Maximal {MAX_ROUTINES} Routinen")
    v = await _clean(db, f, False, allow_trigger)
    r = Routine(**v)
    if not r.interval_min:
        r.interval_min = 60
    r.next_run = None if r.trigger else utcnow() + timedelta(minutes=r.interval_min)
    db.add(r)
    await db.flush()
    return r


async def update(db: AsyncSession, routine_id: str, f: dict, *, allow_trigger: bool = False) -> Routine:
    r = await db.get(Routine, routine_id)
    if not r:
        raise NotFound("Routine nicht gefunden")
    v = await _clean(db, f, True, allow_trigger)
    resched = ("interval_min" in v and v["interval_min"] != r.interval_min) or (v.get("enabled") and not r.enabled) or "trigger" in v
    for k, val in v.items():
        setattr(r, k, val)
    if resched:
        r.next_run = None if r.trigger else utcnow() + timedelta(minutes=r.interval_min)
    return r


async def remove(db: AsyncSession, routine_id: str) -> None:
    r = await db.get(Routine, routine_id)
    if not r:
        raise NotFound("Routine nicht gefunden")
    await db.delete(r)


def is_running(routine_id: str) -> bool:
    return routine_id in _running


# ---------------- runs ----------------
def enqueue(routine_id: str, via: str = "manual", context: str | None = None) -> None:
    if routine_id in _running:
        raise Invalid("Routine läuft bereits oder wartet in der Warteschlange")
    _running.add(routine_id)
    jobs.submit(f"routine {routine_id}", lambda: _run(routine_id, via, context))


async def _run(routine_id: str, via: str, context: str | None) -> None:
    from .runs import run_agent

    try:
        async with session_scope() as db:
            r = await db.get(Routine, routine_id)
            if not r:
                return
            agent = await db.get(Agent, r.agent_id)
            snap = {"name": r.name, "prompt": r.prompt, "agent_id": r.agent_id, "model": r.model, "account_id": r.account_id,
                    "fallback": r.fallback, "memory": r.memory_enabled, "agent_enabled": bool(agent and agent.enabled)}
        t0 = time.monotonic()
        if not snap["agent_enabled"]:
            from .claude.runner import RunResult
            out = RunResult(ok=False, text="Agent ist pausiert – Lauf übersprungen", tools=[])
        else:
            prompt = f"Routine „{snap['name']}“: {snap['prompt']}"
            if context:
                prompt += f"\n\nDiese Routine wurde durch einen Auslöser gestartet. {context}"
            out = await run_agent(snap["agent_id"], prompt, model=snap["model"], account_id=snap["account_id"],
                                  fallback=snap["fallback"], memory_on=snap["memory"])
        ms = int((time.monotonic() - t0) * 1000)
        await record_run(snap["agent_id"], f"Routine {snap['name']}", out.ok, out.text, out.tools, out.account)
        async with session_scope() as db:
            r = await db.get(Routine, routine_id)
            if not r:
                return
            r.last_run = utcnow()
            if r.enabled and not r.trigger:
                r.next_run = utcnow() + timedelta(minutes=r.interval_min)
            db.add(RoutineRun(routine_id=routine_id, via=via, ok=out.ok, text=out.text[:4000], tools=out.tools, account=out.account, ms=ms))
            old = (await db.scalars(select(RoutineRun.id).where(RoutineRun.routine_id == routine_id).order_by(RoutineRun.at.desc()).offset(MAX_HISTORY))).all()
            if old:
                await db.execute(delete(RoutineRun).where(RoutineRun.id.in_(old)))
    finally:
        _running.discard(routine_id)


# ---------------- triggers ----------------
async def run_trigger_tool(db: AsyncSession, t: dict) -> str:
    c = await db.get(Connector, t["connector"])
    if not c:
        raise McpError("Connector gibt es nicht mehr")
    return await call_tool(c, t["tool"], t.get("args") or {})


async def check(routine_id: str) -> dict:
    """Calls the trigger's tool (no AI) and starts the routine if the condition is met."""
    if routine_id in _checking:
        raise Invalid("Die Prüfung läuft gerade")
    _checking.add(routine_id)
    try:
        async with session_scope() as db:
            r = await db.get(Routine, routine_id)
            if not r or not r.trigger:
                raise Invalid("Diese Routine hat keinen Auslöser")
            t = dict(r.trigger)
            try:
                text, err = await run_trigger_tool(db, t), None
            except (McpError, Exception) as e:  # noqa: BLE001 – network errors of all kinds end up here
                text, err = "", str(e)[:300] or e.__class__.__name__
            t["last_check"] = utcnow().isoformat()
            if err:
                t["last_error"] = err
                r.trigger = t
                return {"ok": False, "error": err}
            t["last_error"] = None
            t["last_preview"] = text[:300]
            ev = triggers.evaluate(t, text)
            t["last_note"] = ev["note"]
            started = False
            if ev["fire"]:
                try:
                    enqueue(routine_id, "trigger", ev.get("context"))
                    started = True
                    t["state"], t["last_fired"] = ev["state"], t["last_check"]
                except Invalid:
                    t["last_note"] = ev["note"] + " – Routine läuft noch, nächste Prüfung startet sie erneut"
            else:
                t["state"] = ev["state"]
            r.trigger = t  # reassign: JSON columns are not change-tracked in place
            return {"ok": True, "started": started, "note": t["last_note"], "preview": t["last_preview"]}
    finally:
        _checking.discard(routine_id)


async def due_routines() -> tuple[list[str], list[str]]:
    """(routines to run now, routines whose trigger check is due)"""
    now = utcnow()
    run, chk = [], []
    async with session_scope() as db:
        for r in (await db.scalars(select(Routine).where(Routine.enabled.is_(True)))).all():
            if r.trigger:
                last = r.trigger.get("last_check")
                every = r.trigger.get("every_sec", 300)
                if not last or (now - _parse(last)).total_seconds() >= every:
                    chk.append(r.id)
            elif r.next_run and _aware(r.next_run) <= now and r.id not in _running:
                run.append(r.id)
    return run, chk


def _parse(iso: str):
    from datetime import datetime
    return _aware(datetime.fromisoformat(iso))


def _aware(d):
    from datetime import timezone
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


async def check_quietly(routine_id: str) -> None:
    try:
        await check(routine_id)
    except Invalid:
        pass
    except Exception:  # noqa: BLE001
        log.exception("trigger check %s failed", routine_id)


def schedule_tick() -> None:
    """Called by the scheduler every few seconds."""
    async def tick():
        run, chk = await due_routines()
        for rid in run:
            try:
                enqueue(rid, "schedule")
            except Invalid:
                pass
        for rid in chk:
            asyncio.get_running_loop().create_task(check_quietly(rid))
    return tick()
