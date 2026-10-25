"""Reminders: things that happen exactly once („erinnere mich morgen um 9 an …“, „mach das am Freitag“).

Routines repeat; reminders fire once and are then done (kept a while so the user sees what happened).
  remind: no AI at all – the text arrives as a notification for the user.
  task:   the agent carries out the text at that moment, the result arrives as a notification.
"""
import logging
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_scope, utcnow
from ..models import USER, Agent, Message, Reminder
from . import jobs, schedule
from .agents import record_run
from .errors import Invalid, NotFound

log = logging.getLogger("ida.reminders")
KINDS = ("remind", "task")
MAX_PENDING = 200
KEEP_DONE = timedelta(days=14)
_firing: set[str] = set()


def when_text(r: Reminder) -> str:
    return schedule.describe_once(r.due_at).replace("einmalig ", "")


def short(text: str, n: int = 60) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1] + "…"


async def _clean(db: AsyncSession, f: dict, partial: bool) -> dict:
    v: dict = {}
    if not partial or "text" in f:
        t = str(f.get("text") or "").strip()
        if not t or len(t) > 4000:
            raise Invalid("Text: 1–4000 Zeichen")
        v["text"] = t
    if not partial or f.get("kind") is not None:
        k = f.get("kind") or "remind"
        if k not in KINDS:
            raise Invalid("kind: remind (nur erinnern) oder task (Agent erledigt es)")
        v["kind"] = k
    if not partial or "when" in f:
        if not f.get("when"):
            raise Invalid("Zeitpunkt fehlt (JJJJ-MM-TTTHH:MM)")
        when = schedule.parse_local(f["when"])
        if when < utcnow() - timedelta(minutes=5):
            raise Invalid("Der Zeitpunkt liegt in der Vergangenheit")
        v["due_at"] = when
    if "agent_id" in f or not partial:
        aid = f.get("agent_id")
        agent = await db.get(Agent, aid) if aid else await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
        if not agent:
            raise Invalid("Agent nicht gefunden")
        v["agent_id"] = agent.id
    return v


async def create(db: AsyncSession, f: dict, created_by: str = USER) -> Reminder:
    if await db.scalar(select(func.count()).select_from(Reminder).where(Reminder.status == "pending")) >= MAX_PENDING:
        raise Invalid(f"Höchstens {MAX_PENDING} offene Erinnerungen")
    r = Reminder(**await _clean(db, f, False), created_by=created_by)
    db.add(r)
    await db.flush()
    return r


async def get(db: AsyncSession, reminder_id: str) -> Reminder:
    r = await db.get(Reminder, reminder_id)
    if not r:
        raise NotFound("Erinnerung nicht gefunden")
    return r


async def update(db: AsyncSession, reminder_id: str, f: dict) -> Reminder:
    r = await get(db, reminder_id)
    if r.status == "running":
        raise Invalid("Läuft gerade – gleich nochmal versuchen")
    v = await _clean(db, f, True)
    for k, val in v.items():
        setattr(r, k, val)
    if "due_at" in v:  # new time: it is open again (also after it already fired)
        r.status, r.result, r.done_at = "pending", "", None
    return r


async def remove(db: AsyncSession, reminder_id: str) -> None:
    await db.delete(await get(db, reminder_id))


async def listing(db: AsyncSession) -> list[Reminder]:
    """Open ones by time, then the finished ones (newest first)."""
    rows = (await db.scalars(select(Reminder))).all()
    open_ = sorted((r for r in rows if r.status in ("pending", "running")), key=lambda r: r.due_at)
    done = sorted((r for r in rows if r.status not in ("pending", "running")), key=lambda r: r.done_at or r.due_at, reverse=True)
    return open_ + done


# ---------------- firing ----------------
async def _notify(agent_id: str, title: str, text: str, priority: str = "high") -> None:
    async with session_scope() as db:  # not via agents.send: a reminder the user asked for never counts against the message limit
        db.add(Message(from_id=agent_id, to_id=USER, kind="notice", priority=priority, title=title[:120], text=text[:4000]))


async def fire(reminder_id: str) -> None:
    """Due (or „Jetzt auslösen“): remind -> notification right away; task -> the agent runs it in the job queue."""
    if reminder_id in _firing:
        raise Invalid("Läuft gerade")
    _firing.add(reminder_id)
    try:
        async with session_scope() as db:
            r = await get(db, reminder_id)
            r.status = "running"
            snap = {"id": r.id, "text": r.text, "kind": r.kind, "agent_id": r.agent_id, "when": when_text(r)}
    except Exception:
        _firing.discard(reminder_id)
        raise
    if snap["kind"] == "remind":
        try:
            await _notify(snap["agent_id"], "⏰ Erinnerung", snap["text"])
            await _finish(snap["id"], True, "Benachrichtigung geschickt")
        finally:
            _firing.discard(reminder_id)
        return
    jobs.submit(f"reminder {reminder_id}", lambda: _run_task(snap))


async def _run_task(snap: dict) -> None:
    from .runs import run_agent

    try:
        async with session_scope() as db:
            agent = await db.get(Agent, snap["agent_id"])
            enabled = bool(agent and agent.enabled)
        if not enabled:
            from .claude.runner import RunResult
            out = RunResult(ok=False, text="Agent ist pausiert – nicht ausgeführt", tools=[])
        else:
            prompt = (f"Einmalige Aufgabe, die für jetzt ({snap['when']}) geplant war: {snap['text']}\n\n"
                      "Erledige sie jetzt. Dein Ergebnis bekommt der Nutzer automatisch als Benachrichtigung – "
                      "schreib es also kurz und direkt an ihn (notify_user ist dafür nicht nötig).")
            out = await run_agent(snap["agent_id"], prompt)
        await record_run(snap["agent_id"], f"Erinnerung {short(snap['text'], 40)}", out.ok, out.text, out.tools, out.account)
        await _notify(snap["agent_id"], ("✅ " if out.ok else "⚠ ") + short(snap["text"]), out.text or "(kein Ergebnis)",
                      "normal" if out.ok else "high")
        await _finish(snap["id"], out.ok, out.text)
    except Exception as e:  # noqa: BLE001 – never leave it "running"
        log.exception("reminder %s failed", snap["id"])
        await _finish(snap["id"], False, f"Fehler: {e.__class__.__name__}")
    finally:
        _firing.discard(snap["id"])


async def _finish(reminder_id: str, ok: bool, text: str) -> None:
    async with session_scope() as db:
        r = await db.get(Reminder, reminder_id)
        if r:
            r.status, r.result, r.done_at = ("done" if ok else "failed"), text[:4000], utcnow()


async def schedule_tick() -> None:
    """Called by the scheduler every few seconds: fire what is due, forget old finished ones."""
    now = utcnow()
    async with session_scope() as db:
        # "running" but not in this process: the server restarted mid-run -> try again
        for r in (await db.scalars(select(Reminder).where(Reminder.status == "running"))).all():
            if r.id not in _firing:
                r.status = "pending"
        await db.flush()
        due = (await db.scalars(select(Reminder.id).where(Reminder.status == "pending", Reminder.due_at <= now))).all()
        await db.execute(delete(Reminder).where(Reminder.status.in_(("done", "failed")), Reminder.done_at < now - KEEP_DONE))
    for rid in due:
        try:
            await fire(rid)
        except Invalid:
            pass
