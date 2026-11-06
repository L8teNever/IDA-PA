"""HTTP-Client zum Tasks-Dienst (eigener Container, eigene Datenbank – IDA spricht nur über diese API mit ihm)."""
import httpx

from ..config import get_settings
from .errors import Invalid, NotFound


class TasksOffline(Exception):
    """Der Tasks-Dienst antwortet nicht."""


def enabled() -> bool:
    return bool(get_settings().tasks_url)


async def request(method: str, path: str, *, by: str = "user", params: dict | None = None, json=None, timeout: float = 10):
    """→ geparstes JSON. 4xx wird zu Invalid/NotFound (mit der Meldung des Dienstes), alles andere zu TasksOffline."""
    s = get_settings()
    base = s.tasks_url.rstrip("/")
    if not base:
        raise TasksOffline("Die Aufgaben-App ist nicht eingerichtet (TASKS_URL fehlt)")
    headers = {"X-Tasks-By": by}
    if s.tasks_ida_token:
        headers["Authorization"] = f"Bearer {s.tasks_ida_token}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, f"{base}{path}", params=params, json=json, headers=headers)
    except httpx.HTTPError as e:
        raise TasksOffline("Aufgaben-App nicht erreichbar") from e
    if r.status_code >= 500:
        raise TasksOffline(f"Aufgaben-App meldet einen Fehler ({r.status_code})")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text
        except ValueError:
            detail = r.text
        if isinstance(detail, list):  # Pydantic: [{"loc": …, "msg": …}]
            detail = "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail if isinstance(d, dict))
        detail = detail if isinstance(detail, str) else str(detail)
        if r.status_code == 404:
            raise NotFound(detail)
        raise Invalid(detail)
    return r.json()
