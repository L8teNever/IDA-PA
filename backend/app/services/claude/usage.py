"""Subscription limits of a Claude account (undocumented /api/oauth/usage of Claude Code).

The endpoint rate-limits hard, so results are cached per account, concurrent requests share one fetch and
after a 429 we back off and return the last known values.
"""
import asyncio
import json
import time
from dataclasses import dataclass, field

import httpx

from ...models import Account
from .runner import account_dir

TTL = 180


@dataclass
class _State:
    data: dict | None = None
    at: float = 0
    retry_until: float = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_state: dict[str, _State] = {}


def _token(account: Account) -> str | None:
    if account.oauth_token:
        return account.oauth_token
    try:
        j = json.loads((account_dir(account.id) / ".credentials.json").read_text(encoding="utf8"))
        return (j.get("claudeAiOauth") or {}).get("accessToken")
    except (OSError, ValueError):
        return None


def logged_in(account: Account) -> bool:
    return bool(_token(account))


async def _fetch(account: Account) -> dict:
    token = _token(account)
    if not token:
        return {"error": "Nicht angemeldet – .credentials.json hochladen oder Token eintragen."}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://api.anthropic.com/api/oauth/usage", headers={
            "authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "user-agent": "claude-code/2.1.306"})
    if r.status_code == 429:
        return {"rate_limited": int(r.headers.get("retry-after") or 120)}
    if r.status_code >= 400:
        return {"error": f"Abfrage fehlgeschlagen (HTTP {r.status_code})"}
    b = r.json()
    windows = []

    def add(wid, label, w):
        if w:
            windows.append({"id": wid, "label": label, "percent": w.get("utilization"), "resets_at": w.get("resets_at")})

    add("five_hour", "Aktuelle Sitzung (5 Std)", b.get("five_hour"))
    add("seven_day", "Woche", b.get("seven_day"))
    for lim in b.get("limits") or []:
        if lim.get("kind") == "weekly_scoped":
            name = ((lim.get("scope") or {}).get("model") or {}).get("display_name") or ""
            windows.append({"id": f"scoped_{name}", "label": f"Woche {name}".strip(), "percent": lim.get("percent"), "resets_at": lim.get("resets_at")})
    return {"windows": windows}


async def usage(account: Account, force: bool = False) -> dict:
    st = _state.setdefault(account.id, _State())
    now = time.time()

    def stale(note: str) -> dict:
        return {**st.data, "fetched_at": st.at, "stale": True, "note": note} if st.data else {"error": note}

    if st.data and not force and now - st.at < TTL:
        return {**st.data, "fetched_at": st.at}
    if now < st.retry_until:
        return stale("Abfrage pausiert (zu viele Anfragen bei Anthropic)")
    async with st.lock:
        if st.data and not force and time.time() - st.at < TTL:
            return {**st.data, "fetched_at": st.at}
        try:
            r = await _fetch(account)
        except httpx.HTTPError as e:
            return stale(f"Abfrage fehlgeschlagen: {e}")
    if "rate_limited" in r:
        st.retry_until = time.time() + max(r["rate_limited"], 60)
        return stale("Abfrage pausiert (zu viele Anfragen bei Anthropic)")
    if "error" in r:
        return stale(r["error"])
    st.data, st.at = r, time.time()
    return {**r, "fetched_at": st.at}


async def exhausted(account: Account) -> bool:
    """True if the cached numbers say the account is at 100 % in the session or week window."""
    st = _state.get(account.id)
    if not st or not st.data:
        try:
            await usage(account)
        except Exception:
            return False
        st = _state.get(account.id)
    for w in (st.data or {}).get("windows", []) if st else []:
        if w["id"] in ("five_hour", "seven_day") and (w.get("percent") or 0) >= 99.5:
            return True
    return False
