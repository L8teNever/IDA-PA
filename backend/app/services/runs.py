"""Assembles a Claude run for an agent (prompt, connectors, internal tools) and runs it with account fallback."""
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import session_scope
from ..models import Account, Agent, AgentConnector, Connector, Message, Skill
from .claude import prompts, runner, usage
from .claude.runner import RunResult, RunSpec, norm
from .errors import Invalid
from .memory import store as memory
from .mcp import identity

log = logging.getLogger("ida.runs")
HUB = "ida"  # name of the internal MCP server as the model sees it (mcp__ida__…)


@dataclass
class Prepared:
    spec: RunSpec
    token: str


async def main_agent(db: AsyncSession) -> Agent:
    a = await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
    if not a:
        raise Invalid("Kein Haupt-Agent vorhanden")
    return a


async def accounts(db: AsyncSession) -> list[Account]:
    return list((await db.scalars(select(Account).order_by(Account.position, Account.id))).all())


async def account_or_first(db: AsyncSession, account_id: str | None) -> Account:
    accs = await accounts(db)
    if not accs:
        raise Invalid("Noch kein Claude-Account eingerichtet (Einstellungen → Accounts)")
    return next((a for a in accs if a.id == account_id), accs[0])


async def _connectors(db: AsyncSession, agent: Agent) -> tuple[dict, list[str], list[str]]:
    """MCP servers + allow/deny list: only connectors (and tools) granted to this agent."""
    servers, allow, deny = {}, [], []
    rows = (await db.execute(select(AgentConnector, Connector).join(Connector, Connector.id == AgentConnector.connector_id)
                             .where(AgentConnector.agent_id == agent.id, Connector.enabled.is_(True)))).all()
    for ac, c in rows:
        enabled = c.enabled_tools
        granted = [t for t in (ac.tools if ac.tools is not None else enabled) if t in enabled]
        if c.tools and not granted:
            continue
        servers[c.id] = {"type": "http", "url": c.url, **({"headers": c.headers} if c.headers else {})}
        if not c.tools:  # tool list unknown: allow the whole server
            allow.append(f"mcp__{norm(c.id)}")
            continue
        for t in c.tools:
            (allow if t in granted else deny).append(f"mcp__{norm(c.id)}__{norm(t)}")
    return servers, allow, deny


async def prepare(db: AsyncSession, agent: Agent, prompt: str, *, model: str | None = None, account: Account | None = None,
                  interactive: bool = False, depth: int = 0, memory_on: bool | None = None, forced_skill: Skill | None = None,
                  session_id: str | None = None, persist: bool = False, extra: str = "") -> Prepared:
    s = get_settings()
    memory_on = agent.memory_enabled if memory_on is None else memory_on
    labels = await prompts.labels(db)
    main = await main_agent(db)
    unread = await db.scalar(select(func.count()).select_from(Message).where(Message.to_id == agent.id, Message.read_at.is_(None))) or 0
    system = prompts.persona(agent) + prompts.team_section(agent, main.id, unread, interactive)
    system += prompts.skills_section(await prompts.skills_for(db, agent.id), forced_skill)
    if memory_on:
        system += "\n" + await memory.block(db, agent.id, agent.is_main, prompt, labels)
    if agent.notes and not interactive:
        system += f"\nDeine Notizen aus frueheren Laeufen:\n{agent.notes}\n"
    if extra:
        system += "\n" + extra
    system += "\n" + prompts.now_line()  # last: everything above stays the same between runs (prompt cache)
    servers, allow, deny = await _connectors(db, agent)
    token = identity.issue(identity.Identity(agent_id=agent.id, is_main=agent.is_main, depth=depth, memory=memory_on, interactive=interactive))
    servers[HUB] = {"type": "http", "url": f"{s.internal_url}/mcp/{token}"}
    allow.append(f"mcp__{HUB}")
    acc = account or await account_or_first(db, agent.account_id)
    spec = RunSpec(model=model or agent.model or s.default_model, account=acc, prompt=prompt, system=system,
                   mcp_servers=servers, allow=allow, deny=deny, session_id=session_id, persist=persist,
                   agent_id=agent.id, kind="chat" if interactive else "run")
    return Prepared(spec=spec, token=token)


@asynccontextmanager
async def prepared(*args, **kw):
    p = await prepare(*args, **kw)
    try:
        yield p
    finally:
        identity.revoke(p.token)


async def run_agent(agent_id: str, prompt: str, *, model: str | None = None, account_id: str | None = None,
                    fallback: bool | None = None, memory_on: bool | None = None, depth: int = 0) -> RunResult:
    """Background run of an agent. If the account is at its limit, the SAME model runs on the next account."""
    async with session_scope() as db:
        agent = await db.get(Agent, agent_id)
        if not agent:
            return RunResult(ok=False, text="Agent nicht gefunden", tools=[])
        accs = await accounts(db)
        if not accs:
            return RunResult(ok=False, text="Kein Claude-Account eingerichtet", tools=[])
        preferred = next((a for a in accs if a.id == (account_id or agent.account_id)), accs[0])
        use_fallback = agent.fallback if fallback is None else fallback
        order = [preferred] + ([a for a in accs if a.id != preferred.id] if use_fallback else [])
    notes: list[str] = []
    result: RunResult | None = None
    for i, acc in enumerate(order):
        last = i == len(order) - 1
        if not last and await usage.exhausted(acc):
            notes.append(f"{acc.name} ist am Limit")
            continue
        async with session_scope() as db:
            agent = await db.get(Agent, agent_id)
            p = await prepare(db, agent, prompt, model=model, account=acc, depth=depth, memory_on=memory_on)
        try:
            result = await runner.run(p.spec)
        finally:
            identity.revoke(p.token)
        if result.ok or not result.limit_hit or last:
            break
        notes.append(f"{acc.name} ist am Limit")
    assert result is not None
    if notes:
        result.text = f"↪ {', '.join(notes)} – lief auf {result.account}.\n" + result.text
    return result
