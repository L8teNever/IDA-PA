"""Model -> JSON for the browser. Secrets (tokens, URLs with tokens) never leave the server unmasked."""
from datetime import datetime, timezone

from ..models import Account, Agent, AgentRun, Chat, ChatMessage, Connector, MemoryEntry, Message, Routine, RoutineRun, Skill
from ..services.mcp.client import mask_url


def iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    return (d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d).isoformat()


def agent(a: Agent, extra: dict | None = None) -> dict:
    return {"id": a.id, "is_main": a.is_main, "name": a.name, "description": a.description, "intro": a.intro, "palette": a.palette,
            "home": a.home, "chips": a.chips, "role": a.role, "personality": a.personality, "instructions": a.instructions,
            "notes": a.notes, "model": a.model, "account_id": a.account_id, "fallback": a.fallback, "memory_enabled": a.memory_enabled,
            "enabled": a.enabled, "position": a.position, **(extra or {})}


def agent_run(r: AgentRun) -> dict:
    return {"id": r.id, "trigger": r.trigger, "ok": r.ok, "text": r.text, "tools": r.tools, "account": r.account, "at": iso(r.at)}


def account(a: Account, logged_in: bool) -> dict:
    return {"id": a.id, "name": a.name, "logged_in": logged_in, "has_token": bool(a.oauth_token)}


def connector(c: Connector, agents: list[str] | None = None) -> dict:
    url, secret = mask_url(c.url)
    return {"id": c.id, "name": c.name, "url": url, "auth": secret or bool(c.headers), "enabled": c.enabled, "tools": c.tools,
            "disabled_tools": c.disabled_tools, "status": c.status, "status_text": c.status_text, "checked_at": iso(c.checked_at),
            **({"agents": agents} if agents is not None else {})}


def skill(s: Skill, agents: list[str] | None = None) -> dict:
    return {"id": s.id, "name": s.name, "icon": s.icon, "description": s.description, "body": s.body, "enabled": s.enabled,
            **({"agents": agents} if agents is not None else {})}


def chat(c: Chat, last: ChatMessage | None = None) -> dict:
    out = {"id": c.id, "agent_id": c.agent_id, "title": c.title, "model": c.model, "account_id": c.account_id,
           "created_at": iso(c.created_at), "updated_at": iso(c.updated_at)}
    if last is not None:
        out["last"] = {"role": last.role, "text": (last.text or "")[:200]}
    return out


def chat_message(m: ChatMessage) -> dict:
    return {"id": m.id, "role": m.role, "text": m.text, "parts": m.parts, "meta": m.meta, "at": iso(m.at)}


def message(m: Message) -> dict:
    return {"id": m.id, "from": m.from_id, "to": m.to_id, "kind": m.kind, "priority": m.priority, "title": m.title, "text": m.text,
            "at": iso(m.at), "read": m.read_at is not None}


def routine(r: Routine, last: RoutineRun | None = None, running: bool = False) -> dict:
    t = dict(r.trigger) if r.trigger else None
    if t:
        t["seen"] = len((t.pop("state", None) or {}).get("keys") or [])
    return {"id": r.id, "name": r.name, "icon": r.icon, "prompt": r.prompt, "agent_id": r.agent_id, "model": r.model,
            "account_id": r.account_id, "interval_min": r.interval_min, "times": r.times, "days": r.days, "run_at": iso(r.run_at), "trigger": t, "fallback": r.fallback,
            "memory_enabled": r.memory_enabled, "enabled": r.enabled, "next_run": iso(r.next_run), "last_run": iso(r.last_run),
            "running": running, "last": routine_run(last) if last else None}


def routine_run(r: RoutineRun) -> dict:
    return {"id": r.id, "via": r.via, "ok": r.ok, "text": r.text, "tools": r.tools, "account": r.account, "ms": r.ms, "at": iso(r.at)}


def memory_entry(e: MemoryEntry, labels: dict) -> dict:
    return {"id": e.id, "scope": e.scope, "text": e.text, "type": e.type, "pinned": e.pinned, "importance": e.importance,
            "by": e.by, "by_label": labels.get(e.by, e.by), "at": iso(e.at), "updated_at": iso(e.updated_at),
            "expires": iso(e.expires), "uses": e.uses, "embedded": bool(e.embedding)}
