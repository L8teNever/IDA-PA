"""Fixed run times for routines ("07:00 and 18:30, Mon–Fri"), in the user's time zone (TIMEZONE, default Europe/Berlin)."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import get_settings
from .errors import Invalid

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]  # Python weekday(): Monday = 0
MAX_TIMES = 24


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def clean_times(times) -> list[str]:
    if isinstance(times, str):
        times = re.split(r"[,\s;]+", times)
    out = set()
    for t in times or []:
        m = TIME_RE.match(str(t).strip())
        if not m:
            raise Invalid(f"Uhrzeit „{t}“ ungültig – Format HH:MM, z. B. 07:30")
        out.add(f"{int(m.group(1)):02d}:{m.group(2)}")
    if not out:
        raise Invalid("Mindestens eine Uhrzeit angeben")
    if len(out) > MAX_TIMES:
        raise Invalid(f"Höchstens {MAX_TIMES} Uhrzeiten")
    return sorted(out)


def clean_days(days) -> list[int]:
    if days is None or days == []:
        return list(range(7))  # every day
    out = sorted({int(d) for d in days})
    if any(d < 0 or d > 6 for d in out):
        raise Invalid("Wochentage: 0 (Montag) bis 6 (Sonntag)")
    return out


def parse_local(value) -> datetime:
    """'2026-10-26T09:00' (local time, TIMEZONE) or an ISO time with offset -> UTC."""
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip().replace("Z", "+00:00").replace(" ", "T"))
    except ValueError as e:
        raise Invalid(f"Zeitpunkt „{value}“ ungültig – Format JJJJ-MM-TTTHH:MM, z. B. 2026-10-26T09:00") from e
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz())
    return d.astimezone(timezone.utc)


def describe_once(when: datetime) -> str:
    """'einmalig am So 26.10. 09:00'"""
    d = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(tz())
    return f"einmalig am {DAY_NAMES[d.weekday()]} {d:%d.%m.} {d:%H:%M}"


def next_run(times: list[str], days: list[int] | None, after: datetime | None = None) -> datetime:
    """Next point in time (UTC) after `after` that matches one of the times on an allowed weekday."""
    zone = tz()
    after = (after or datetime.now(timezone.utc)).astimezone(zone)
    allowed = set(days if days else range(7))
    for offset in range(8):
        day = (after + timedelta(days=offset)).date()
        if day.weekday() not in allowed:
            continue
        for t in times:
            h, m = map(int, t.split(":"))
            # wall-clock time in the user's zone (handles summer/winter time)
            cand = datetime(day.year, day.month, day.day, h, m, tzinfo=zone)
            if cand > after:
                return cand.astimezone(timezone.utc)
    raise Invalid("Kein nächster Termin gefunden")  # only with an empty day list


def describe(times: list[str], days: list[int] | None) -> str:
    """'täglich 07:00, 18:30' / 'Mo–Fr 07:00' / 'Mo, Mi 09:15'"""
    d = sorted(days if days else range(7))
    if d == list(range(7)):
        when = "täglich"
    elif d == list(range(5)):
        when = "Mo–Fr"
    elif d == [5, 6]:
        when = "Sa+So"
    else:
        when = ", ".join(DAY_NAMES[i] for i in d)
    return f"{when} {', '.join(times)}"
