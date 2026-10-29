"""Ortszeit ⇄ UTC, Datums-Eingaben und Bereiche wie „heute“, „diese Woche“.

Alles in der Datenbank ist UTC; nach außen gibt es Ortszeit mit Offset. Eingaben ohne Offset gelten als Ortszeit.
"""
import re
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from ..config import get_settings
from ..errors import Invalid

UTC = timezone.utc
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WEEKDAYS_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
MONTHS_SHORT = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."]


@lru_cache(maxsize=32)
def zone(name: str | None = None) -> ZoneInfo:
    try:
        return ZoneInfo(name or get_settings().timezone)
    except Exception as e:  # noqa: BLE001
        raise Invalid(f"Unbekannte Zeitzone: {name}") from e


def now() -> datetime:
    return datetime.now(UTC)


def local_now() -> datetime:
    return now().astimezone(zone())


def to_local(dt: datetime, tzid: str | None = None) -> datetime:
    return dt.astimezone(zone(tzid))


def local_to_utc(naive: datetime, tzid: str | None = None) -> datetime:
    """Ortszeit ohne Zone → UTC (bei der Zeitumstellung: nicht existierende Zeit rutscht vor, doppelte nimmt die erste)."""
    return naive.replace(tzinfo=zone(tzid), fold=0).astimezone(UTC)


def midnight_utc(d: date, tzid: str | None = None) -> datetime:
    return local_to_utc(datetime.combine(d, time.min), tzid)


def iso(dt: datetime, tzid: str | None = None) -> str:
    """UTC → „2026-10-29T15:00:00+01:00“ (Ortszeit)."""
    return to_local(dt, tzid).isoformat(timespec="seconds")


_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_input(value, tzid: str | None = None) -> tuple[datetime, bool]:
    """„2026-10-29“, „2026-10-29T15:00“, „2026-10-29 15:00“, mit/ohne Offset → (UTC, ist_nur_datum)."""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=zone(tzid))).astimezone(UTC), False
    if isinstance(value, date):
        return midnight_utc(value, tzid), True
    s = str(value or "").strip()
    if not s:
        raise Invalid("Zeitangabe fehlt")
    if _DATE.match(s):
        try:
            return midnight_utc(date.fromisoformat(s), tzid), True
        except ValueError as e:
            raise Invalid(f"Ungültiges Datum: {s}") from e
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError as e:
        raise Invalid(f"Ungültige Zeitangabe: {s} (erwartet JJJJ-MM-TT oder JJJJ-MM-TTTHH:MM)") from e
    return (d if d.tzinfo else d.replace(tzinfo=zone(tzid))).astimezone(UTC), False


def day_bounds(d: date, tzid: str | None = None) -> tuple[datetime, datetime]:
    """Beginn und Ende eines Ortstages in UTC (23 bzw. 25 Stunden bei der Zeitumstellung)."""
    return midnight_utc(d, tzid), midnight_utc(d + timedelta(days=1), tzid)


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def parse_range(text: str, base: date | None = None) -> tuple[datetime, datetime]:
    """„heute“, „morgen“, „diese woche“, „nächste woche“, „JJJJ-MM-TT“, „JJJJ-MM“ → (von, bis) in UTC; bis ist exklusiv."""
    t = (text or "").strip().lower()
    today = base or local_now().date()
    if t in ("heute", "today", ""):
        a, b = today, today + timedelta(days=1)
    elif t in ("morgen", "tomorrow"):
        a, b = today + timedelta(days=1), today + timedelta(days=2)
    elif t in ("gestern", "yesterday"):
        a, b = today - timedelta(days=1), today
    elif t in ("übermorgen", "uebermorgen"):
        a, b = today + timedelta(days=2), today + timedelta(days=3)
    elif t in ("diese woche", "woche", "this week"):
        a = monday(today); b = a + timedelta(days=7)
    elif t in ("nächste woche", "naechste woche", "next week"):
        a = monday(today) + timedelta(days=7); b = a + timedelta(days=7)
    elif t in ("letzte woche", "last week"):
        a = monday(today) - timedelta(days=7); b = a + timedelta(days=7)
    elif t in ("diesen monat", "dieser monat", "monat", "this month"):
        a = today.replace(day=1); b = add_months(a, 1)
    elif t in ("nächsten monat", "naechsten monat", "next month"):
        a = add_months(today.replace(day=1), 1); b = add_months(a, 1)
    elif re.match(r"^\d{4}-\d{2}$", t):
        y, m = int(t[:4]), int(t[5:])
        if not 1 <= m <= 12:
            raise Invalid(f"Ungültiger Monat: {text}")
        a = date(y, m, 1); b = add_months(a, 1)
    elif _DATE.match(t):
        a = date.fromisoformat(t); b = a + timedelta(days=1)
    else:
        raise Invalid(f"Zeitraum nicht verstanden: „{text}“ (z. B. heute, morgen, diese Woche, 2026-10-29, 2026-10)")
    return midnight_utc(a), midnight_utc(b)


def next_midnight(dt: datetime, tzid: str | None = None) -> datetime:
    """Beginn des nächsten Ortstages nach dt (bei der Zeitumstellung nicht einfach +24 Stunden)."""
    return midnight_utc(dt.astimezone(zone(tzid)).date() + timedelta(days=1), tzid)


def range_from(frm: str | None, to: str | None) -> tuple[datetime, datetime]:
    """von/bis aus einer Anfrage: Wörter („heute“) oder Datum/Zeit. Ist nur `von` ein Datum/Wort, gilt der ganze Zeitraum davon;
    ist `bis` ein reines Datum, ist der ganze Tag eingeschlossen."""
    if not frm:
        return parse_range("heute")
    try:
        a, a_date = parse_input(frm)
        a_end = next_midnight(a) if a_date else a
    except Invalid:
        a, a_end = parse_range(frm)
    if not to:
        if a_end == a:
            return a, a + timedelta(days=1)
        return a, a_end
    try:
        b, b_date = parse_input(to)
        if b_date:
            b = next_midnight(b)
    except Invalid:
        _, b = parse_range(to)
    if b <= a:
        raise Invalid("Das Ende liegt vor dem Anfang")
    return a, b


def fmt_day(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {d.day}. {MONTHS_SHORT[d.month - 1]}"


def fmt_when(start: datetime, end: datetime, all_day: bool, tzid: str | None = None) -> str:
    """Kurztext für Tool-Antworten: „Do, 30. Okt. 15:00–16:00“ oder „Do, 30. Okt. (ganztägig)“."""
    s, e = to_local(start, tzid), to_local(end, tzid)
    if all_day:
        last = (e - timedelta(days=1)).date()
        if last > s.date():
            return f"{fmt_day(s.date())} – {fmt_day(last)} (ganztägig)"
        return f"{fmt_day(s.date())} (ganztägig)"
    if e.date() == s.date():
        return f"{fmt_day(s.date())} {s:%H:%M}–{e:%H:%M}"
    return f"{fmt_day(s.date())} {s:%H:%M} – {fmt_day(e.date())} {e:%H:%M}"
