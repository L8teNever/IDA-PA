"""HTTP-Client zum Kalender-Dienst (eigener Container, eigene Datenbank – IDA spricht nur über diese API mit ihm)."""
import time
from datetime import datetime

import httpx

from ..config import get_settings
from .errors import Invalid, NotFound
from .schedule import tz


class KalenderOffline(Exception):
    """Der Kalender-Dienst antwortet nicht."""


def enabled() -> bool:
    return bool(get_settings().kalender_url)


def _headers(by: str) -> dict:
    s = get_settings()
    h = {"X-Kalender-By": by}
    if s.kalender_ida_token:
        h["Authorization"] = f"Bearer {s.kalender_ida_token}"
    return h


async def request(method: str, path: str, *, by: str = "user", params: dict | None = None, json=None, files=None, data=None, timeout: float = 10):
    """→ geparstes JSON. 4xx wird zu Invalid/NotFound (mit der Meldung des Kalenders), alles andere zu KalenderOffline."""
    base = get_settings().kalender_url.rstrip("/")
    if not base:
        raise KalenderOffline("Der Kalender ist nicht eingerichtet (KALENDER_URL fehlt)")
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, f"{base}{path}", params=params, json=json, files=files, data=data, headers=_headers(by))
    except httpx.HTTPError as e:
        raise KalenderOffline("Kalender-Dienst nicht erreichbar") from e
    if r.status_code >= 500:
        raise KalenderOffline(f"Kalender-Dienst meldet einen Fehler ({r.status_code})")
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
        raise Invalid(detail)
    try:
        return r.json()
    except ValueError:
        return {}


# ---------------- Termine für Startseite und Prompt (kurz zwischengespeichert) ----------------
_cache: dict[str, tuple[float, list]] = {}
TTL = 60


def invalidate() -> None:
    _cache.clear()


async def events(frm: str, to: str | None = None, *, by: str = "user", cache: bool = True) -> list[dict]:
    key = f"{by}|{frm}|{to}"
    hit = _cache.get(key)
    if cache and hit and time.time() - hit[0] < TTL:
        return hit[1]
    got = (await request("GET", "/api/v1/events", by=by, params={"from": frm, "to": to} if to else {"from": frm}))["events"]
    _cache[key] = (time.time(), got)
    return got


async def today(*, cache: bool = True) -> list[dict]:
    d = datetime.now(tz()).date().isoformat()
    return await events(d, d, cache=cache)
