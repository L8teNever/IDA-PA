"""Feiertage – lokal berechnet (Paket `holidays`), nichts wird heruntergeladen."""
from datetime import datetime, timedelta

import holidays as _h

from ..models import Calendar
from .timeutil import zone

_cache: dict[tuple, object] = {}


def _country(state: str, year: int):
    key = (state, year)
    if key not in _cache:
        try:
            _cache[key] = _h.country_holidays("DE", subdiv=state or None, years=year, language="de")
        except (NotImplementedError, KeyError):
            _cache[key] = _h.country_holidays("DE", years=year, language="de")
    return _cache[key]


def occurrences(start: datetime, end: datetime, state: str, cal: Calendar) -> list[dict]:
    zi = zone()
    d0, d1 = start.astimezone(zi).date(), (end - timedelta(seconds=1)).astimezone(zi).date()
    out, d = [], d0
    while d <= d1:
        name = _country(state, d.year).get(d)
        if name:
            out.append({"id": f"hol_{d:%Y%m%d}", "event_id": f"hol_{d:%Y%m%d}", "calendar_id": cal.id, "calendar": cal.name, "title": name.split(" (")[0],
                        "start": d.isoformat(), "end": (d + timedelta(days=1)).isoformat(), "all_day": True, "location": "", "color": cal.color,
                        "recurring": False, "recurrence_text": "", "alarms": [], "status": "confirmed", "transparency": "free", "travel_min": 0,
                        "created_by": "system", "updated_by": "system", "read_only": True, "has_description": False, "holiday": True})
        d += timedelta(days=1)
    return out
