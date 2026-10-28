"""Who is calling the internal MCP server: one secret token per run, revoked when the run ends."""
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    agent_id: str
    is_main: bool
    depth: int = 0  # length of the chain of messages that led to this run
    memory: bool = True  # memory tools available
    interactive: bool = False  # a live chat with the user (management tools only then)
    chat_id: str | None = None  # the chat this run answers in
    code: bool = False  # a code thread (approvals go to the user through code_approve)


_tokens: dict[str, Identity] = {}


def issue(identity: Identity) -> str:
    t = secrets.token_hex(16)
    _tokens[t] = identity
    return t


def get(token: str) -> Identity | None:
    return _tokens.get(token)


def revoke(token: str) -> None:
    _tokens.pop(token, None)
