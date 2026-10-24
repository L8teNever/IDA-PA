"""Agents: create/update/delete, tool access, messages between agents (and to the user) and wake-ups."""
import asyncio
import logging
import re
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import USER, Agent, AgentConnector, AgentRun, Chat, Connector, Message, Routine
from . import jobs
from .claude import prompts
from .errors import Invalid, NotFound
from .memory import store as memory
from .skills import slugify

log = logging.getLogger("ida.agents")
PALETTES = ("blau", "pink", "mint", "gelb", "lila", "orange")
MAX_AGENTS = 12
MAX_DEPTH = 4  # chains of messages triggering each other
MAX_PER_HOUR = 30  # messages per sender
MAX_NOTES = 4000
USER_ALIASES = {"user", "nutzer", "du"}


# ---------------- agents ----------------
def _clean(f: dict, partial: bool) -> dict:
    s = get_settings()
    v: dict = {}

    def text(key: str, max_len: int, required: bool = False):
        if key in f or (required and not partial):
            t = str(f.get(key) or "").strip()
            if required and not t:
                raise Invalid(f"{key}: darf nicht leer sein")
            if len(t) > max_len:
                raise Invalid(f"{key}: höchstens {max_len} Zeichen")
            v[key] = t

    text("name", 40, required=True)
    text("description", 120)
    text("intro", 300)
    text("role", 4000)
    text("personality", 2000)
    text("instructions", 4000)
    if f.get("model") is not None or not partial:
        m = f.get("model") or s.default_model
        if m not in s.model_list:
            raise Invalid(f"model muss eines von {', '.join(s.model_list)} sein")
        v["model"] = m
    if "account_id" in f:
        v["account_id"] = f["account_id"] or None
    if f.get("palette") is not None:
        if f["palette"] not in PALETTES:
            raise Invalid(f"palette: {', '.join(PALETTES)}")
        v["palette"] = f["palette"]
    if f.get("home") is not None:
        if f["home"] not in ("timeline", "simple"):
            raise Invalid("home: timeline oder simple")
        v["home"] = f["home"]
    if f.get("chips") is not None:
        chips = [[str(c[0])[:40], str(c[1])[:80]] for c in f["chips"] if isinstance(c, (list, tuple)) and len(c) >= 2][:6]
        v["chips"] = chips
    for b in ("fallback", "memory_enabled", "enabled"):
        if f.get(b) is not None:
            v[b] = bool(f[b])
    return v


async def create(db: AsyncSession, f: dict) -> Agent:
    v = _clean(f, False)
    if await db.scalar(select(func.count()).select_from(Agent)) >= MAX_AGENTS:
        raise Invalid(f"Maximal {MAX_AGENTS} Agenten")
    if await db.scalar(select(Agent).where(func.lower(Agent.name) == v["name"].lower())):
        raise Invalid("Den Namen gibt es schon")
    base = slugify(str(f.get("id") or v["name"]))[:24] or "agent"
    aid, n = base, 2
    while aid in USER_ALIASES or aid in ("shared", "consolidate") or await db.get(Agent, aid):
        aid, n = f"{base}-{n}", n + 1
    pos = (await db.scalar(select(func.max(Agent.position)))) or 0
    a = Agent(id=aid, is_main=bool(f.get("is_main")), position=pos + 1, **{"intro": v.get("description", ""), **v})
    db.add(a)
    await db.flush()
    for cid in f.get("connectors") or []:
        if await db.get(Connector, cid):
            db.add(AgentConnector(agent_id=aid, connector_id=cid, tools=None))
    return a


async def get(db: AsyncSession, agent_id: str) -> Agent:
    a = await db.get(Agent, agent_id)
    if not a:
        raise NotFound("Agent nicht gefunden")
    return a


async def update_agent(db: AsyncSession, agent_id: str, f: dict) -> Agent:
    a = await get(db, agent_id)
    v = _clean(f, True)
    if "name" in v and await db.scalar(select(Agent).where(func.lower(Agent.name) == v["name"].lower(), Agent.id != agent_id)):
        raise Invalid("Den Namen gibt es schon")
    if a.is_main and v.get("enabled") is False:
        raise Invalid("Der Haupt-Agent kann nicht pausiert werden")
    for k, val in v.items():
        setattr(a, k, val)
    return a


async def remove(db: AsyncSession, agent_id: str) -> None:
    a = await get(db, agent_id)
    if a.is_main:
        raise Invalid("Der Haupt-Agent kann nicht gelöscht werden")
    main = await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
    # keep history: chats and routines go to the main agent (routines paused)
    await db.execute(update(Chat).where(Chat.agent_id == agent_id).values(agent_id=main.id))
    await db.execute(update(Routine).where(Routine.agent_id == agent_id).values(agent_id=main.id, enabled=False))
    await memory.drop_scope(db, agent_id)
    await db.delete(a)


async def set_notes(db: AsyncSession, agent_id: str, text: str) -> str:
    a = await get(db, agent_id)
    a.notes = str(text or "")[:MAX_NOTES]
    return a.notes


async def set_access(db: AsyncSession, agent_id: str, connector_id: str, allowed: bool, tools: list[str] | None = None) -> None:
    """Grant (all enabled tools or only `tools`) or revoke a connector for an agent. Only the user does this."""
    await get(db, agent_id)
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    await db.execute(delete(AgentConnector).where(AgentConnector.agent_id == agent_id, AgentConnector.connector_id == connector_id))
    if allowed:
        db.add(AgentConnector(agent_id=agent_id, connector_id=connector_id, tools=None if tools is None else [t for t in tools if t in c.tools]))


async def record_run(agent_id: str, trigger: str, ok: bool, text: str, tools: list[str], account: str | None) -> None:
    async with session_scope() as db:
        db.add(AgentRun(agent_id=agent_id, trigger=trigger[:80], ok=ok, text=text[:4000], tools=tools, account=account))
        old = (await db.scalars(select(AgentRun.id).where(AgentRun.agent_id == agent_id).order_by(AgentRun.at.desc()).offset(20))).all()
        if old:
            await db.execute(delete(AgentRun).where(AgentRun.id.in_(old)))


# ---------------- messages ----------------
async def resolve(db: AsyncSession, to: str) -> str | None:
    t = str(to or "").strip().lower()
    if t in USER_ALIASES:
        return USER
    if t in ("main", "haupt", "haupt-agent", "hauptagent", "chat"):
        main = await db.scalar(select(Agent.id).where(Agent.is_main.is_(True)))
        return main
    if await db.get(Agent, t):
        return t
    return await db.scalar(select(Agent.id).where(func.lower(Agent.name) == t))


async def send(db: AsyncSession, *, from_id: str, to: str, text: str, priority: str = "normal", expect_reply: bool = False,
               depth: int = 0, kind: str | None = None, title: str = "") -> Message:
    body = str(text or "").strip()
    if not body:
        raise Invalid("text fehlt")
    target = await resolve(db, to)
    if not target:
        ids = (await db.scalars(select(Agent.id))).all()
        raise Invalid(f"Unbekannter Empfänger „{to}“. Gültig: user, {', '.join(ids)}")
    if target == from_id:
        raise Invalid("Nicht an sich selbst senden")
    if depth > MAX_DEPTH:
        raise Invalid(f"Weiterleitungs-Limit ({MAX_DEPTH}) erreicht – Nachrichtenkette wird gestoppt")
    if from_id != USER:
        sent = await db.scalar(select(func.count()).select_from(Message).where(Message.from_id == from_id, Message.at >= utcnow() - timedelta(hours=1)))
        if sent >= MAX_PER_HOUR:
            raise Invalid(f"Limit erreicht: höchstens {MAX_PER_HOUR} Nachrichten pro Stunde")
    m = Message(from_id=from_id, to_id=target, text=body[:4000], title=str(title or "")[:120], priority="high" if priority == "high" else "normal",
                kind=kind or ("task" if expect_reply else "message"), expect_reply=expect_reply, depth=depth)
    db.add(m)
    await db.flush()
    if target != USER:
        wake_later(target)
    return m


async def unread(db: AsyncSession, to: str) -> list[Message]:
    return list((await db.scalars(select(Message).where(Message.to_id == to, Message.read_at.is_(None)).order_by(Message.at))).all())


async def mark_read(db: AsyncSession, ids: list[str]) -> None:
    if ids:
        await db.execute(update(Message).where(Message.id.in_(ids), Message.read_at.is_(None)).values(read_at=utcnow()))


# ---------------- wake-ups ----------------
_wake_queued: set[str] = set()


def wake_later(agent_id: str) -> None:
    """A message wakes the agent: one queued run handles everything unread. Main agent mail waits for the chat."""
    if agent_id in _wake_queued:
        return
    _wake_queued.add(agent_id)
    try:
        jobs.submit(f"wake {agent_id}", lambda: _wake(agent_id))
    except RuntimeError:  # no running loop (e.g. CLI import)
        _wake_queued.discard(agent_id)


async def _wake(agent_id: str) -> None:
    from .runs import run_agent  # late import: runs → prompts → … (keeps import graph simple)

    _wake_queued.discard(agent_id)
    await asyncio.sleep(1)  # the request that sent the message commits first
    async with session_scope() as db:
        agent = await db.get(Agent, agent_id)
        if not agent or not agent.enabled or agent.is_main:
            return
        msgs = await unread(db, agent_id)
        if not msgs:
            return
        await mark_read(db, [m.id for m in msgs])
        labels = await prompts.labels(db)
        snapshot = [(m.from_id, m.text, m.kind, m.priority, m.expect_reply) for m in msgs]
        depth = max(m.depth for m in msgs)
    lines = []
    for frm, text, kind, prio, _ in snapshot:
        how = "AUFGABE, Antwort erwartet – deine Endausgabe wird automatisch zurueckgeschickt" if kind == "task" else "WICHTIG" if prio == "high" else "Nachricht"
        lines.append(f"- Von {labels.get(frm, frm)} ({how}): {text}")
    prompt = "Du hast neue Nachrichten:\n" + "\n".join(lines) + "\n\nBearbeite sie jetzt."
    try:
        out = await run_agent(agent_id, prompt, depth=depth)
    except Exception as e:  # noqa: BLE001
        log.exception("wake %s failed", agent_id)
        from .claude.runner import RunResult
        out = RunResult(ok=False, text=str(e), tools=[])
    await record_run(agent_id, "Nachricht", out.ok, out.text, out.tools, out.account)
    async with session_scope() as db:
        for frm, _, _, prio, expect in snapshot:
            if expect:
                try:
                    await send(db, from_id=agent_id, to=frm, text=out.text or "(kein Ergebnis)", depth=depth + 1, kind="result", priority=prio)
                except Invalid as e:
                    log.warning("reply from %s not sent: %s", agent_id, e)


def valid_id(s: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9-]{1,32}", s or ""))
