"""Tage verstehen: „2026-11-12“, „12.11.“, „heute“, „morgen“, „übermorgen“, „freitag“, „nächste woche“, „in 3 tagen“ …

Die Oberfläche schickt immer ISO-Daten. Die Wörter sind für Ida und Stella, damit sie nicht selbst rechnen müssen.
"""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import get_settings

_WD = {"mo": 0, "montag": 0, "di": 1, "dienstag": 1, "mi": 2, "mittwoch": 2, "do": 3, "donnerstag": 3, "fr": 4, "freitag": 4,
       "sa": 5, "samstag": 5, "so": 6, "sonntag": 6}
_UML = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


class BadDay(ValueError):
    pass


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def parse(raw, base: date | None = None) -> date | None:
    """None/„“ → None. Unbekanntes → BadDay."""
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    t = " ".join(str(raw).strip().lower().translate(_UML).split())
    if not t or t in ("null", "none", "keiner", "kein", "nie", "-"):
        return None
    base = base or today()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        try:
            return date.fromisoformat(t)
        except ValueError as e:
            raise BadDay(f"Kein gültiges Datum: {raw}") from e
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?", t)
    if m:
        d, mo, y = int(m[1]), int(m[2]), m[3]
        year = (2000 + int(y) if len(y) == 2 else int(y)) if y else base.year
        try:
            out = date(year, mo, d)
        except ValueError as e:
            raise BadDay(f"Kein gültiges Datum: {raw}") from e
        return out.replace(year=out.year + 1) if not y and out < base else out
    fixed = {"heute": 0, "jetzt": 0, "sofort": 0, "morgen": 1, "uebermorgen": 2}
    if t in fixed:
        return base + timedelta(days=fixed[t])
    m = re.fullmatch(r"in (\d{1,3}|einem|einer|zwei|drei|vier|fuenf) (tag|tagen|woche|wochen)", t)
    if m:
        n = {"einem": 1, "einer": 1, "zwei": 2, "drei": 3, "vier": 4, "fuenf": 5}.get(m[1]) or int(m[1])
        return base + timedelta(days=n * (7 if m[2].startswith("woche") else 1))
    if t in ("naechste woche", "kommende woche"):
        return base + timedelta(days=7 - base.weekday())  # nächster Montag
    if t in ("wochenende", "am wochenende", "dieses wochenende"):
        return base + timedelta(days=(5 - base.weekday()) % 7)
    t = re.sub(r"^(am|naechsten|naechster|kommenden|kommender) ", "", t)
    if t in _WD:
        ahead = (_WD[t] - base.weekday()) % 7 or 7  # „freitag“ am Freitag = in einer Woche
        return base + timedelta(days=ahead)
    raise BadDay(f"Tag nicht verstanden: {raw} (z. B. 2026-11-12, morgen, freitag)")
