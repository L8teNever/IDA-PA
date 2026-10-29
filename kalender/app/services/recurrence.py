"""Wiederholungen: RRULE auflösen, Ausnahmen, Scopes. Reine Funktionen, ohne Datenbank.

Die Regel läuft in der Ortszeit des Termins („jeden Montag 08:00“ bleibt nach der Zeitumstellung um 08:00).
UNTIL steht bei uns immer als Ortszeit ohne „Z“ („UNTIL=20261220T235959“); beim ICS-Export wird es nach UTC umgerechnet.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from ..errors import Invalid
from .timeutil import MONTHS_SHORT, UTC, WEEKDAYS, zone

DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
FREQS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
KEY_FMT = "%Y%m%dT%H%M"
MAX_COUNT = 5000
_DAY_ALIASES = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6, "tu": 1, "we": 2, "th": 3, "su": 6,
                "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3, "freitag": 4, "samstag": 5, "sonntag": 6}


@dataclass
class Occ:
    key: str            # Beginn in Ortszeit des Termins: „20261029T1500“
    start: datetime     # UTC
    end: datetime       # UTC


def parse(rrule: str) -> dict[str, str]:
    out = {}
    for part in (rrule or "").replace("RRULE:", "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().upper()] = v.strip().upper()
    return out


def build(parts: dict[str, str]) -> str:
    order = ["FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYSETPOS", "COUNT", "UNTIL"]
    keys = [k for k in order if k in parts] + [k for k in parts if k not in order]
    return ";".join(f"{k}={parts[k]}" for k in keys if parts[k] != "")


def _until_to_local(v: str, tzid: str) -> str:
    """UNTIL aus ICS/Eingabe → Ortszeit ohne Z; reines Datum = bis Tagesende."""
    v = v.strip()
    if re.fullmatch(r"\d{8}", v):
        return v + "T235959"
    m = re.fullmatch(r"(\d{8}T\d{6})(Z?)", v)
    if not m:
        raise Invalid(f"Ungültiges Ende der Wiederholung: {v}")
    if m.group(2):
        dt = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC).astimezone(zone(tzid))
        return dt.strftime("%Y%m%dT%H%M%S")
    return m.group(1)


def normalize(rrule: str, tzid: str = "Europe/Berlin") -> str:
    """Prüft und vereinheitlicht eine Regel. Leer bleibt leer."""
    p = parse(rrule)
    if not p:
        return ""
    if p.get("FREQ") not in FREQS:
        raise Invalid("Wiederholung: nur täglich, wöchentlich, monatlich und jährlich werden unterstützt")
    try:
        if int(p.get("INTERVAL", "1")) < 1:
            raise ValueError
        if "COUNT" in p and not 1 <= int(p["COUNT"]) <= MAX_COUNT:
            raise ValueError
    except ValueError as e:
        raise Invalid("Wiederholung: Intervall und Anzahl müssen positive Zahlen sein (Anzahl höchstens 5000)") from e
    if "UNTIL" in p:
        p["UNTIL"] = _until_to_local(p["UNTIL"], tzid)
        p.pop("COUNT", None)
    for k in ("WKST",):
        p.pop(k, None)
    out = build(p)
    _rule(out, datetime(2000, 1, 1))  # wirft bei Unsinn
    return out


def weekday_index(v) -> int:
    if isinstance(v, int):
        if 0 <= v <= 6:
            return v
    else:
        i = _DAY_ALIASES.get(str(v).strip().lower().rstrip("."))
        if i is not None:
            return i
    raise Invalid(f"Unbekannter Wochentag: {v} (Mo, Di, Mi, Do, Fr, Sa, So oder 0–6)")


def build_rrule(repeat: str, interval: int = 1, days=None, until: str | None = None, count: int | None = None, nth: int | None = None) -> str:
    """Einfache Felder → RRULE: repeat = daily | weekly | monthly | yearly | weekdays."""
    r = (repeat or "").strip().lower()
    names = {"daily": "DAILY", "täglich": "DAILY", "taeglich": "DAILY", "weekly": "WEEKLY", "wöchentlich": "WEEKLY", "woechentlich": "WEEKLY",
             "monthly": "MONTHLY", "monatlich": "MONTHLY", "yearly": "YEARLY", "jährlich": "YEARLY", "jaehrlich": "YEARLY"}
    p: dict[str, str] = {}
    if r in ("weekdays", "werktags"):
        p = {"FREQ": "WEEKLY", "BYDAY": "MO,TU,WE,TH,FR"}
    elif r in names:
        p["FREQ"] = names[r]
    elif r in ("", "none", "nie"):
        return ""
    else:
        raise Invalid(f"Unbekannte Wiederholung: {repeat} (daily, weekly, monthly, yearly, weekdays)")
    if interval and int(interval) > 1:
        p["INTERVAL"] = str(int(interval))
    if days:
        idx = sorted({weekday_index(d) for d in (days if isinstance(days, (list, tuple)) else [days])})
        if nth and p["FREQ"] in ("MONTHLY", "YEARLY"):
            p["BYDAY"] = ",".join(f"{int(nth)}{DAYS[i]}" for i in idx)
        else:
            p["BYDAY"] = ",".join(DAYS[i] for i in idx)
    if until:
        p["UNTIL"] = re.sub(r"[-:]", "", str(until)).replace(" ", "T")
        if len(p["UNTIL"]) == 8:
            p["UNTIL"] += "T235959"
        elif len(p["UNTIL"]) == 13:
            p["UNTIL"] += "00"
    elif count:
        p["COUNT"] = str(int(count))
    return normalize(build(p))


def _rule(rrule: str, dtstart: datetime):
    try:
        return rrulestr(rrule, dtstart=dtstart)
    except (ValueError, TypeError, KeyError) as e:
        raise Invalid(f"Ungültige Wiederholung „{rrule}“: {e}") from e


def local_start(start_at: datetime, tzid: str) -> datetime:
    return start_at.astimezone(zone(tzid)).replace(tzinfo=None)


def key_of(local: datetime) -> str:
    return local.strftime(KEY_FMT)


def parse_key(key: str) -> datetime:
    try:
        return datetime.strptime(key, KEY_FMT)
    except ValueError as e:
        raise Invalid(f"Ungültige Termin-ID (Zeit): {key}") from e


def to_utc(local: datetime, tzid: str) -> datetime:
    return local.replace(tzinfo=zone(tzid), fold=0).astimezone(UTC)


def _span(ev, loc_start: datetime, zi: ZoneInfo) -> tuple[datetime, datetime]:
    """Anfang/Ende eines Vorkommens in UTC. Ganztägig rechnet in Kalendertagen, sonst bleibt die Dauer gleich."""
    s = loc_start.replace(tzinfo=zi, fold=0).astimezone(UTC)
    if ev.all_day:
        days = max(1, (ev.end_at.astimezone(zi).date() - ev.start_at.astimezone(zi).date()).days)
        e = datetime.combine(loc_start.date() + timedelta(days=days), time.min).replace(tzinfo=zi, fold=0).astimezone(UTC)
    else:
        e = s + (ev.end_at - ev.start_at)
    return s, e


def expand(ev, start: datetime, end: datetime) -> list[Occ]:
    """Alle Vorkommen eines Termins, die [start, end) berühren (UTC)."""
    if not ev.rrule:
        return [Occ(key_of(local_start(ev.start_at, ev.tzid)), ev.start_at, ev.end_at)] if ev.start_at < end and ev.end_at > start else []
    zi = zone(ev.tzid)
    first = local_start(ev.start_at, ev.tzid)
    rule = _rule(ev.rrule, first)
    dur = ev.end_at - ev.start_at
    lo = start.astimezone(zi).replace(tzinfo=None) - dur - timedelta(days=1)
    hi = end.astimezone(zi).replace(tzinfo=None)
    skip = set(ev.exdates or [])
    out = []
    for d in rule.between(lo, hi, inc=True):
        k = key_of(d)
        if k in skip:
            continue
        s, e = _span(ev, d, zi)
        if s < end and e > start:
            out.append(Occ(k, s, e))
    return out


def occurrence_id(event_id: str, ev, occ: Occ) -> str:
    return event_id if not ev.rrule else f"{event_id}_{occ.key}"


def split_id(occ_id: str) -> tuple[str, str | None]:
    """„<id>“ → (id, None); „<id>_<20261029T1500>“ → (id, Schlüssel)."""
    if "_" not in occ_id:
        return occ_id, None
    eid, key = occ_id.split("_", 1)
    parse_key(key)
    return eid, key


def series_end(rrule: str, start_at: datetime, end_at: datetime, tzid: str, all_day: bool = False) -> datetime | None:
    """Ende des letzten Vorkommens (UTC); None = endlos."""
    if not rrule:
        return None
    p = parse(rrule)
    first = local_start(start_at, tzid)
    rule = _rule(rrule, first)
    last = None
    if "COUNT" in p:
        for last in rule:  # höchstens MAX_COUNT
            pass
    elif "UNTIL" in p:
        last = rule.before(datetime.strptime(p["UNTIL"], "%Y%m%dT%H%M%S"), inc=True)
    else:
        return None
    if last is None:
        return end_at
    zi = zone(tzid)

    class _E:  # minimal, damit _span wie bei echten Terminen rechnet
        pass
    e = _E(); e.all_day, e.start_at, e.end_at = all_day, start_at, end_at
    return _span(e, last, zi)[1]


def count_before(rrule: str, start_at: datetime, tzid: str, cut_local: datetime) -> int:
    rule = _rule(rrule, local_start(start_at, tzid))
    return sum(1 for _ in rule.between(datetime(1970, 1, 1), cut_local - timedelta(seconds=1), inc=True))


def cut_until(rrule: str, start_at: datetime, tzid: str, cut_local: datetime) -> tuple[str, str]:
    """„Dieser und folgende“: (alte Regel mit Ende vor cut_local, Regel für die neue Serie ab cut_local)."""
    p = parse(rrule)
    old = dict(p); old.pop("COUNT", None)
    old["UNTIL"] = (cut_local - timedelta(seconds=1)).strftime("%Y%m%dT%H%M%S")
    new = dict(p)
    if "COUNT" in p:
        remaining = int(p["COUNT"]) - count_before(rrule, start_at, tzid, cut_local)
        new["COUNT"] = str(max(1, remaining))
    return build(old), build(new)


# ---------------- Klartext ----------------
def _ord(n: int) -> str:
    return f"{n}."


def describe(rrule: str) -> str:
    """„Jeden Mo und Mi bis 20. Dez.“ – Klartext für Oberfläche und Tool-Antworten."""
    p = parse(rrule)
    if not p:
        return ""
    iv = int(p.get("INTERVAL", "1"))
    f = p["FREQ"]
    by = [x for x in p.get("BYDAY", "").split(",") if x]
    if f == "DAILY":
        txt = "Täglich" if iv == 1 else f"Alle {iv} Tage"
    elif f == "WEEKLY":
        days = [WEEKDAYS[DAYS.index(d[-2:])] for d in by]
        if sorted(by) == sorted(["MO", "TU", "WE", "TH", "FR"]) and iv == 1:
            txt = "Werktags"
        elif not days:
            txt = "Wöchentlich" if iv == 1 else f"Alle {iv} Wochen"
        else:
            lst = days[0] if len(days) == 1 else ", ".join(days[:-1]) + " und " + days[-1]
            txt = (f"Jeden {lst}" if iv == 1 else f"Alle {iv} Wochen am {lst}")
    elif f == "MONTHLY":
        if by and re.match(r"^-?\d", by[0]):
            n = int(re.match(r"^-?\d+", by[0]).group())
            wd = WEEKDAYS[DAYS.index(by[0][-2:])]
            txt = f"Monatlich am {'letzten' if n == -1 else _ord(n)} {wd}"
        elif p.get("BYMONTHDAY"):
            txt = f"Monatlich am {_ord(int(p['BYMONTHDAY'].split(',')[0]))}"
        else:
            txt = "Monatlich" if iv == 1 else f"Alle {iv} Monate"
    else:
        txt = "Jährlich" if iv == 1 else f"Alle {iv} Jahre"
    if "UNTIL" in p:
        d = datetime.strptime(p["UNTIL"], "%Y%m%dT%H%M%S").date()
        txt += f" bis {d.day}. {MONTHS_SHORT[d.month - 1]}" + (f" {d.year}" if d.year != date.today().year else "")
    elif "COUNT" in p:
        txt += f", {p['COUNT']} Mal"
    return txt
