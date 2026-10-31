"""HTTP-Client zum Wiki-Dienst (eigener Container, eigene Vault+DB – IDA spricht nur über diese API mit ihm)."""
import time

import httpx

from ..config import get_settings
from .errors import Invalid, NotFound


class WikiOffline(Exception):
    """Der Wiki-Dienst antwortet nicht."""


def enabled() -> bool:
    return bool(get_settings().wiki_url)


def public_url() -> str:
    return (get_settings().wiki_public_url or "").rstrip("/")


def _headers(by: str) -> dict:
    s = get_settings()
    h = {"X-Wiki-By": by}
    if s.wiki_ida_token:
        h["Authorization"] = f"Bearer {s.wiki_ida_token}"
    return h


async def request(method: str, path: str, *, by: str = "user", params: dict | None = None, json=None, timeout: float = 15):
    """→ geparstes JSON. 4xx wird zu Invalid/NotFound; Netz-/5xx → WikiOffline."""
    base = get_settings().wiki_url.rstrip("/")
    if not base:
        raise WikiOffline("Das Wiki ist nicht eingerichtet (WIKI_URL fehlt)")
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, f"{base}{path}", params=params, json=json, headers=_headers(by))
    except httpx.HTTPError as e:
        raise WikiOffline("Wiki-Dienst nicht erreichbar") from e
    if r.status_code >= 500:
        raise WikiOffline(f"Wiki-Dienst meldet einen Fehler ({r.status_code})")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text
        except ValueError:
            detail = r.text
        detail = detail if isinstance(detail, str) else str(detail)
        if r.status_code == 404:
            raise NotFound(detail)
        if r.status_code == 403:
            raise Invalid(f"Keine Berechtigung: {detail}")
        if r.status_code == 409:
            raise Invalid(detail)
        raise Invalid(detail)
    try:
        return r.json()
    except ValueError:
        return {}


# ---------------- Memory-Kontext für System-Prompts (kurz zwischengespeichert) ----------------
_ctx_cache: dict[str, tuple[float, str]] = {}
TTL = 60


def invalidate() -> None:
    _ctx_cache.clear()


async def memory_context(agent_id: str, query: str = "", *, max_chars: int = 3800) -> str:
    """Kompakter Block aus memory/kern + agenten/<id> + relevante Treffer. Bei Ausfall: leerer String."""
    if not enabled():
        return ""
    key = f"{agent_id}|{query[:80]}|{max_chars}"
    hit = _ctx_cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        data = await request(
            "GET",
            "/api/v1/memory/context",
            by=agent_id or "main",
            params={"agent_id": agent_id or "main", "q": query or "", "max_chars": max_chars},
            timeout=8,
        )
    except (WikiOffline, Invalid, NotFound):
        return ""
    text = (data.get("text") or data.get("context") or "").strip()
    if text:
        _ctx_cache[key] = (time.time(), text)
    return text
