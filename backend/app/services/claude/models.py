"""All Claude models the accounts can use – read from Anthropic's model list (/v1/models) with an account's login.

The list is cached in data/claude/models.json and refreshed every few hours in the background. The access
token of an account is only valid for a while (the CLI renews it when it runs) – with an expired token the
cached list stays. Without any list the configured MODELS are used.
"""
import asyncio
import json
import logging
import time

import httpx

from ...config import get_settings
from ...models import Account
from .usage import _token

log = logging.getLogger("ida.claude.models")
MAX_AGE = 6 * 3600
_task: asyncio.Task | None = None


def _file():
    return get_settings().claude_dir / "models.json"


def catalog() -> list[dict]:
    try:
        return json.loads(_file().read_text(encoding="utf8")).get("models") or []
    except (OSError, ValueError):
        return []


def ids() -> list[str]:
    s = get_settings()
    found = [m["id"] for m in catalog()]
    # configured models first (default on top), then everything else the API lists, newest first as delivered
    return list(dict.fromkeys([m for m in s.model_list if m in found or not found] + found))


def names() -> dict[str, str]:
    return {m["id"]: m.get("name") or m["id"] for m in catalog()}


async def refresh(accounts: list[Account]) -> list[dict]:
    for a in accounts:
        tok = _token(a)
        if not tok:
            continue
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get("https://api.anthropic.com/v1/models", params={"limit": 100}, headers={
                    "authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20", "anthropic-version": "2023-06-01",
                    "user-agent": "claude-code/2.1.306"})
        except httpx.HTTPError:
            continue
        if r.status_code != 200:
            continue
        models = [{"id": m["id"], "name": m.get("display_name") or m["id"]} for m in r.json().get("data") or [] if m.get("id")]
        if models:
            _file().write_text(json.dumps({"models": models, "checked": time.time()}, indent=1), encoding="utf8")
            log.info("claude model list: %d models", len(models))
            return models
    return catalog()


def refresh_in_background(accounts: list[Account]) -> None:
    """Called when the app loads: renews the list if it is old (does not block the request)."""
    global _task
    try:
        checked = json.loads(_file().read_text(encoding="utf8")).get("checked", 0)
    except (OSError, ValueError):
        checked = 0
    if time.time() - checked < MAX_AGE or (_task and not _task.done()):
        return
    claude = [a for a in accounts if a.provider == "claude"]
    if claude:
        _task = asyncio.get_running_loop().create_task(refresh(claude))
