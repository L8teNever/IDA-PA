"""Schnelleingabe und natürliche Zeitangaben – nur Regeln, ohne KI, ohne Netz.

quick("morgen 15 Uhr Zahnarzt 1h @Praxis Müller #Privat !30m") → Termin-Vorschlag mit markierten Textteilen (spans).
extract_range("zahnarzt nächste woche") → ("zahnarzt", Zeitraum).
"""
import re
from datetime import date, datetime, time, timedelta

from . import recurrence as rec
from .timeutil import MONTHS, MONTHS_SHORT, WEEKDAYS_LONG, WEEKDAYS, add_months, iso, local_now, midnight_utc, monday, parse_range, zone

_WD_FULL = {n.lower(): i for i, n in enumerate(WEEKDAYS_LONG)}
_WD_SHORT = {n.lower(): i for i, n in enumerate(WEEKDAYS)}
_WD_ANY = "|".join(sorted(list(_WD_FULL) + list(_WD_SHORT), key=len, reverse=True))
_WD_FULL_RX = "|".join(sorted(_WD_FULL, key=len, reverse=True))
_MONTH_RX = "|".join(m.lower() for m in MONTHS)
_MONTH_IDX = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}
_MONTH_IDX.update({"maerz": 3, "jan": 1, "feb": 2, "mär": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dez": 12})
FLAGS = re.I


class _Work:
    """Der Text, aus dem erkannte Teile nach und nach herausgenommen werden; die Positionen bleiben die des Originals."""

    def __init__(self, text: str):
        self.text, self.work, self.spans = text, text, []

    def undo(self, m) -> None:
        """Den letzten take() zurücknehmen (der Treffer war doch keine Zeit)."""
        self.spans.pop()
        self.work = self.work[:m.start()] + self.text[m.start():m.end()] + self.work[m.end():]

    def take(self, pattern: str, kind: str, flags=FLAGS):
        m = re.search(pattern, self.work, flags)
        if not m:
            return None
        self.spans.append({"kind": kind, "start": m.start(), "end": m.end(), "text": self.text[m.start():m.end()].strip()})
        self.work = self.work[:m.start()] + " " * (m.end() - m.start()) + self.work[m.end():]
        return m


def _hm(h: str, m: str | None) -> time | None:
    hh, mm = int(h), int(m or 0)
    return time(hh, mm) if 0 <= hh <= 23 and 0 <= mm <= 59 else None


def _next_weekday(today: date, wd: int, strict: bool) -> date:
    ahead = (wd - today.weekday()) % 7
    return today + timedelta(days=ahead or (7 if strict else 0))


def _year(y: str | None, d: int, m: int, today: date) -> int:
    if y:
        return int(y) + (2000 if len(y) == 2 else 0)
    try:
        cand = date(today.year, m, d)
    except ValueError:
        return today.year
    return today.year if cand >= today else today.year + 1


def _alarm_minutes(n: str, unit: str | None) -> int:
    u = (unit or "m").lower()
    k = 1440 if u in ("d", "t", "tag", "tage", "tagen") else 60 if u in ("h", "std", "stunde", "stunden") else 1
    return int(n) * k


def quick(text: str, now: datetime | None = None, calendars: list[str] | None = None, default_min: int = 60) -> dict:
    now = (now or local_now()).astimezone(zone())
    today = now.date()
    w = _Work(text)
    out: dict = {"calendar": None, "location": "", "alarms": [], "rrule": "", "all_day": False}

    m = w.take(r"(?<!\S)#([\wäöüÄÖÜß-]+)", "calendar")
    if m:
        name = m.group(1)
        out["calendar"] = next((c for c in (calendars or []) if c.lower() == name.lower()), name)
    while True:
        m = w.take(r"(?<!\S)!(\d+)\s*(min|m|h|std|d|tage?n?|t)?\b", "alarm")
        if not m:
            break
        out["alarms"].append(_alarm_minutes(m.group(1), m.group(2)))
    out["alarms"] = sorted(set(out["alarms"]))

    # Wiederholung
    rr, repeat_days, interval, freq = None, None, 1, None
    m = w.take(rf"\bjeden\s+((?:{_WD_ANY})(?:\s*(?:,|und)\s*(?:{_WD_ANY}))*)\b", "repeat")
    if m:
        freq = "weekly"
        repeat_days = [(_WD_FULL.get(x) if x in _WD_FULL else _WD_SHORT[x]) for x in re.findall(_WD_ANY, m.group(1).lower())]
    else:
        m = w.take(r"\balle\s+(\d+)\s+(tage|wochen|monate|jahre)\b", "repeat")
        if m:
            interval = int(m.group(1))
            freq = {"tage": "daily", "wochen": "weekly", "monate": "monthly", "jahre": "yearly"}[m.group(2).lower()]
        else:
            m = w.take(r"\b(jeden\s+tag|t[äa]glich|werktags|w[öo]chentlich|monatlich|j[äa]hrlich)\b", "repeat")
            if m:
                x = m.group(1).lower().replace("ä", "a").replace("ö", "o")
                freq = "weekdays" if x == "werktags" else "daily" if x in ("jeden tag", "taglich") else "weekly" if x.startswith("wochen") else \
                    "monthly" if x == "monatlich" else "yearly"
    until = count = None
    if freq:
        m = w.take(r"\b(?:bis(?:\s+zum)?|until)\s+(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?(?!\d)", "until")
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            until = date(_year(m.group(3), d, mo, today), mo, d).isoformat()
        else:
            m = w.take(r"\b(\d+)\s*(?:mal|x)\b", "until")
            if m:
                count = int(m.group(1))

    if w.take(r"\bganzt(?:ä|ae)gig\b|\bganztags\b", "allday"):
        out["all_day"] = True

    # Uhrzeit(en)
    start_t = end_t = None
    m = w.take(r"(?:\bvon\s+)?(?<![\d:.])(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?\s*(?:-|–|bis)\s*(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?(?![\d.:])", "time")
    if m and not (m.group(2) or m.group(4) or "uhr" in m.group(0).lower() or m.group(0).lower().startswith("von")):
        w.undo(m)  # „8-9“ ohne Uhr/Doppelpunkt ist keine Zeit
        m = None
    if m:
        start_t, end_t = _hm(m.group(1), m.group(2)), _hm(m.group(3), m.group(4))
    # Dauer
    dur = None
    m = w.take(r"(?:\bf[üu]r\s+)?(?<![\d:.])(\d+(?:[.,]\d+)?)\s*(h|std\.?|stunden?|min\.?|minuten?)(?![a-zäöü])", "duration")
    if m:
        n = float(m.group(1).replace(",", "."))
        dur = int(n * (60 if m.group(2).lower().startswith(("h", "s")) else 1))
    if start_t is None:
        m = w.take(r"(?:\b(?:um|ab|gegen)\s+)?(?<![\d:.])(\d{1,2})[:.](\d{2})(?:\s*uhr)?(?![\d:])", "time")
        if m and ("." in m.group(0) and ":" not in m.group(0)) and "uhr" not in m.group(0).lower():
            w.undo(m)  # „12.30“ ohne „Uhr“ ist eher ein Datum
            m = None
        if m:
            start_t = _hm(m.group(1), m.group(2))
    if start_t is None:
        m = w.take(r"\bhalb\s+(\d{1,2})\b", "time")
        if m:
            h = int(m.group(1)) - 1
            h = h + 12 if 0 < h < 7 else h
            start_t = _hm(str(h), "30")
    if start_t is None:
        m = w.take(r"(?:\b(?:um|ab|gegen)\s+)?(?<![\d:.])(\d{1,2})\s*uhr\b", "time")
        if m:
            start_t = _hm(m.group(1), None)
    if start_t is None:
        m = w.take(r"\bum\s+(\d{1,2})\b(?![.:\d])", "time")
        if m:
            start_t = _hm(m.group(1), None)

    # Datum
    day = None
    if w.take(r"\b[üu]bermorgen\b|\buebermorgen\b", "date"):
        day = today + timedelta(days=2)
    elif w.take(r"\bmorgen\b", "date"):
        day = today + timedelta(days=1)
    elif w.take(r"\bheute\b", "date"):
        day = today
    if day is None:
        m = w.take(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?(?![\d])", "date")
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            try:
                day = date(_year(m.group(3), d, mo, today), mo, d)
            except ValueError:
                day = None
    if day is None:
        m = w.take(rf"(?<![\d.])(\d{{1,2}})\.\s*({_MONTH_RX})\.?(?:\s+(\d{{4}}))?", "date")
        if m:
            d, mo = int(m.group(1)), _MONTH_IDX[m.group(2).lower()]
            try:
                day = date(_year(m.group(3), d, mo, today), mo, d)
            except ValueError:
                day = None
    if day is None:
        m = w.take(r"\bin\s+(\d+)\s+(tag(?:en)?|wochen?)\b", "date")
        if m:
            day = today + timedelta(days=int(m.group(1)) * (7 if m.group(2).lower().startswith("woche") else 1))
    if day is None:
        m = w.take(rf"\b(?:(n[äa]chsten?|kommenden)\s+)?({_WD_FULL_RX})\b|\b(?:(am|n[äa]chsten?)\s+)({_WD_ANY})\b", "date")
        if m:
            name = (m.group(2) or m.group(4)).lower()
            wd = _WD_FULL.get(name, _WD_SHORT.get(name))
            strict = bool(m.group(1) or (m.group(3) and m.group(3).lower().startswith("n")))
            day = _next_weekday(today, wd, strict)
            if not strict and day == today and start_t and datetime.combine(today, start_t) <= now.replace(tzinfo=None):
                day += timedelta(days=7)
    if freq == "weekly" and repeat_days and day is None:
        day = min(_next_weekday(today, d, False) for d in repeat_days)
        if start_t and day == today and datetime.combine(today, start_t) <= now.replace(tzinfo=None):
            day = min(_next_weekday(today, d, True) for d in repeat_days)

    # Ort (zuletzt: alles nach dem @, jetzt ohne Datum/Uhrzeit)
    m = w.take(r"@\s*(.+?)\s*$", "location")
    if m:
        out["location"] = m.group(1).strip()
    title = re.sub(r"\s+", " ", w.work).strip(" ,;-–")
    title = re.sub(r"^(?:um|am|ab|gegen|von|bis|für|in)\s+|\s+(?:um|am|ab|gegen|von|bis|für|in)$", "", title, flags=FLAGS).strip(" ,;-–")
    out["title"] = title

    # Standardwerte
    if day is None:
        day = today if (start_t is None or datetime.combine(today, start_t) > now.replace(tzinfo=None)) else today + timedelta(days=1)
    if start_t is None and not out["all_day"] and dur is None and end_t is None:
        out["all_day"] = True
    if out["all_day"] or start_t is None:
        out["all_day"] = True
        out["start"], out["end"] = day.isoformat(), (day + timedelta(days=1)).isoformat()
    else:
        s = datetime.combine(day, start_t)
        if end_t is not None:
            e = datetime.combine(day, end_t)
            if e <= s:
                e += timedelta(days=1)
        else:
            e = s + timedelta(minutes=dur or default_min)
        out["start"], out["end"] = s.isoformat(timespec="minutes"), e.isoformat(timespec="minutes")
    if freq:
        out["rrule"] = rec.build_rrule(freq if freq != "weekly" or not repeat_days else "weekly", interval, repeat_days, until, count)
        out["recurrence_text"] = rec.describe(out["rrule"])
    out["spans"] = sorted(w.spans, key=lambda s: s["start"])
    return out


# ---------------- Zeitangaben in der Suche ----------------
def extract_range(text: str, now: datetime | None = None) -> tuple[str, tuple[datetime, datetime] | None, str]:
    """Nimmt eine Zeitangabe aus dem Suchtext: → (Rest, (von, bis) | None, Anzeige-Text). bis ist exklusiv (UTC)."""
    base = (now or local_now()).astimezone(zone()).date()
    t = text

    def cut(m) -> str:
        return (t[:m.start()] + " " + t[m.end():]).strip()

    m = re.search(r"\b(heute|morgen|[üu]bermorgen|gestern|diese woche|n[äa]chste woche|letzte woche|diesen monat|n[äa]chsten monat)\b", t, FLAGS)
    if m:
        a, b = parse_range(m.group(1).lower(), base)
        return re.sub(r"\s+", " ", cut(m)), (a, b), m.group(1).capitalize()
    m = re.search(rf"\bim\s+({_MONTH_RX})(?:\s+(\d{{4}}))?\b", t, FLAGS)
    if m:
        mo = _MONTH_IDX[m.group(1).lower()]
        y = int(m.group(2)) if m.group(2) else (base.year if mo >= base.month else base.year + 1)
        a = date(y, mo, 1)
        return re.sub(r"\s+", " ", cut(m)), (midnight_utc(a), midnight_utc(add_months(a, 1))), f"{MONTHS[mo - 1]} {y}"
    m = re.search(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?(?!\d)", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            a = date(_year(m.group(3), d, mo, base), mo, d)
            return re.sub(r"\s+", " ", cut(m)), (midnight_utc(a), midnight_utc(a + timedelta(days=1))), f"{a.day}. {MONTHS_SHORT[a.month - 1]}"
        except ValueError:
            pass
    m = re.search(rf"\b(?:am\s+)?({_WD_FULL_RX})\b", t, FLAGS)
    if m:
        a = _next_weekday(base, _WD_FULL[m.group(1).lower()], False)
        return re.sub(r"\s+", " ", cut(m)), (midnight_utc(a), midnight_utc(a + timedelta(days=1))), f"{WEEKDAYS[a.weekday()]}, {a.day}. {MONTHS_SHORT[a.month - 1]}"
    return text, None, ""
