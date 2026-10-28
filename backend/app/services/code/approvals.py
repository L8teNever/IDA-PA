"""Approvals for code threads in "Nachfragen" mode.

The Claude CLI asks the permission tool mcp__ida__code_approve before it edits a file or runs a command.
The request appears in the thread (event {"approval": …} in the running answer, so a browser that comes back
later still sees it) and waits – without a time limit – until the user allows or denies it, or stops the answer.
"""
import asyncio
import json
from dataclasses import dataclass, field

from ...db import utcnow
from ...models._types import new_id

ALLOW, DENY, ALWAYS = "allow", "deny", "always"


@dataclass
class Approval:
    chat_id: str
    tool: str
    input: dict
    id: str = field(default_factory=new_id)
    at: str = field(default_factory=lambda: utcnow().isoformat())
    fut: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())

    def view(self) -> dict:
        return {"id": self.id, "chat_id": self.chat_id, "tool": self.tool, "input": _short(self.input), "at": self.at}


_pending: dict[str, Approval] = {}
_always: dict[str, set[str]] = {}  # chat id -> tools the user allowed for good in this thread


def _short(v: dict) -> dict:
    """Big inputs (whole files for Write) are cut for the card."""
    out = {}
    for k, x in (v or {}).items():
        s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
        out[k] = s[:4000] + "…" if len(s) > 4000 else x
    return out


def always(chat_id: str) -> list[str]:
    return sorted(_always.get(chat_id, ()))


def waiting(chat_id: str) -> list[dict]:
    return [a.view() for a in _pending.values() if a.chat_id == chat_id]


async def ask(chat_id: str, tool: str, tool_input: dict) -> dict:
    """Answer for the CLI's permission prompt: {"behavior": "allow", "updatedInput"} or {"behavior": "deny", "message"}."""
    from .. import chat as chat_service
    if tool in _always.get(chat_id, ()):
        return {"behavior": "allow", "updatedInput": tool_input}
    a = Approval(chat_id=chat_id, tool=tool, input=tool_input)
    _pending[a.id] = a
    await chat_service.push(chat_id, {"approval": a.view()})
    try:
        decision = await a.fut
    finally:
        _pending.pop(a.id, None)
    await chat_service.push(chat_id, {"approval_done": {"id": a.id, "decision": decision}})
    if decision in (ALLOW, ALWAYS):
        return {"behavior": "allow", "updatedInput": tool_input}
    msg = "Der Nutzer hat das abgelehnt. Frag nach, was er stattdessen möchte." if decision == DENY else "Die Antwort wurde gestoppt."
    return {"behavior": "deny", "message": msg}


def answer(approval_id: str, decision: str) -> dict:
    a = _pending.get(approval_id)
    if not a or a.fut.done():
        from ..errors import NotFound
        raise NotFound("Diese Freigabe gibt es nicht mehr")
    if decision == ALWAYS:
        _always.setdefault(a.chat_id, set()).add(a.tool)
    a.fut.set_result(decision)
    return a.view()


def cancel_chat(chat_id: str) -> None:
    """The answer was stopped or ended: open requests of the chat are denied."""
    for a in list(_pending.values()):
        if a.chat_id == chat_id and not a.fut.done():
            a.fut.set_result("stopped")


def forget(chat_id: str) -> None:
    cancel_chat(chat_id)
    _always.pop(chat_id, None)
