"""What the connected MCP servers are – a short text per connector that goes into every agent's context.

Without it an agent only sees tool names and does not know that "Schulapp" or "die Termine aus Sofia"
means the sofia_* tools. A cheap model drafts the text from the tool list, the user can edit it.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...models import AgentConnector, Connector
from ..claude.runner import norm
from . import client

MAX_ONE = 900     # chars per connector in the prompt
MAX_ALL = 3500    # chars for the whole block

PROMPT = (
    "Unten stehen die Tools eines MCP-Servers, den der Nutzer an seine KI-Assistenten angeschlossen hat. "
    "Schreibe einen kurzen Ueberblick auf Deutsch, damit ein Assistent sofort weiss, was dahintersteckt, wenn der Nutzer "
    "umgangssprachlich davon redet (z. B. \"meine Schulapp\", \"die Termine aus Sofia\", \"mein Server\").\n"
    "Format: pro Dienst/App eine Zeile \"- Name (wie der Nutzer es nennen koennte): was es ist und was man damit machen kann "
    "– Tools: praefix_*\". Fasse Tools mit gleichem Praefix zu einem Dienst zusammen. Hoechstens 10 Zeilen, nichts erfinden, "
    "keine Einleitung, nur die Zeilen.\n\nTools:\n"
)


def _tool_lines(defs: list[dict]) -> str:
    out = []
    for d in defs:
        desc = " ".join(str(d.get("description") or "").split())[:140]
        out.append(f"- {d.get('name')}: {desc}")
    return "\n".join(out)[:16000]


async def describe(db: AsyncSession, c: Connector) -> str:
    """Drafts the context text from the server's tools (cheap model). Raises on errors."""
    from .. import runs
    from ..claude import runner
    from ..claude.runner import RunSpec
    defs = await client.tool_defs(c)
    if not defs:
        return ""
    model = get_settings().cheap_model
    acc = await runs.account_or_first(db, None, model)
    out = await runner.run(RunSpec(model=model, account=acc, prompt=PROMPT + _tool_lines(defs),
                                   system="Du beschreibst Software knapp und praezise. Antworte nur mit den Zeilen."))
    if not out.ok:
        raise client.McpError((out.text or "Beschreibung fehlgeschlagen")[:300])
    return out.text.strip()[:2000]


def section(rows: list[tuple[str, str, str]], prefix: str = "mcp__") -> str:
    """rows: (connector id, name, context). Prompt block for the agent; empty if nothing is described.
    prefix: how the agent sees the tool names (Claude: mcp__<id>__tool, Stella/Gemini: <id>__tool)."""
    parts, used = [], 0
    for cid, name, ctx in rows:
        ctx = (ctx or "").strip()
        if not ctx:
            continue
        block = f"[{name} – Tools {prefix}{cid}__*]\n{ctx[:MAX_ONE]}"
        if used + len(block) > MAX_ALL:
            break
        parts.append(block)
        used += len(block)
    if not parts:
        return ""
    return ("Deine verbundenen Dienste (MCP). Redet der Nutzer von einer dieser Apps oder Sachen darin, auch umgangssprachlich "
            "(\"Schulapp\", \"Hausaufgaben\", \"Termine aus Sofia\"), weisst du, was gemeint ist: nimm direkt die passenden Tools "
            "und frag nicht nach, was das sein soll.\n" + "\n".join(parts) + "\n")


async def for_agents(db: AsyncSession, agent_ids: set[str], prefix: str = "mcp__") -> str:
    """Prompt block for the enabled connectors any of these agents may use."""
    rows = (await db.execute(select(Connector.id, Connector.name, Connector.context)
                             .join(AgentConnector, AgentConnector.connector_id == Connector.id)
                             .where(AgentConnector.agent_id.in_(agent_ids), Connector.enabled.is_(True))
                             .order_by(Connector.name).distinct())).all()
    return section([(norm(i), n, c) for i, n, c in rows], prefix)
