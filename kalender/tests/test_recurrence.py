"""Wiederholungen als reine Funktionen: Zeitumstellung, Monatsende, COUNT/UNTIL, Ausnahmen, Klartext."""
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.errors import Invalid
from app.services import recurrence as rec

BE = ZoneInfo("Europe/Berlin")
UTC = timezone.utc


def ev(start_local: datetime, rrule: str, minutes: int = 60, all_day=False, exdates=None):
    s = start_local.replace(tzinfo=BE).astimezone(UTC)
    return SimpleNamespace(start_at=s, end_at=s + __import__("datetime").timedelta(minutes=minutes), all_day=all_day, tzid="Europe/Berlin",
                           rrule=rrule, exdates=exdates or [])


def window(a: str, b: str):
    return datetime.fromisoformat(a).replace(tzinfo=UTC), datetime.fromisoformat(b).replace(tzinfo=UTC)


def starts(occs):
    return [o.start.astimezone(BE).strftime("%Y-%m-%d %H:%M") for o in occs]


def test_weekly_stays_at_8_across_dst_end_of_october():
    # Sommerzeit endet am 25.10.2026 – „jeden Montag 08:00“ bleibt 08:00 Ortszeit (UTC wechselt von 06:00 auf 07:00)
    e = ev(datetime(2026, 10, 19, 8, 0), "FREQ=WEEKLY;BYDAY=MO")
    got = rec.expand(e, *window("2026-10-18", "2026-11-03"))
    assert starts(got) == ["2026-10-19 08:00", "2026-10-26 08:00", "2026-11-02 08:00"]
    assert [o.start.hour for o in got] == [6, 7, 7]


def test_daily_across_dst_start_of_march():
    # Sommerzeit beginnt am 28.03.2027
    e = ev(datetime(2027, 3, 26, 9, 30), "FREQ=DAILY;COUNT=4")
    got = rec.expand(e, *window("2027-03-25", "2027-04-05"))
    assert starts(got) == ["2027-03-26 09:30", "2027-03-27 09:30", "2027-03-28 09:30", "2027-03-29 09:30"]
    assert [o.start.hour for o in got] == [8, 8, 7, 7]


def test_monthly_on_31st_skips_short_months():
    e = ev(datetime(2026, 1, 31, 10, 0), "FREQ=MONTHLY;BYMONTHDAY=31;COUNT=6")
    assert starts(rec.expand(e, *window("2026-01-01", "2026-12-31"))) == [
        "2026-01-31 10:00", "2026-03-31 10:00", "2026-05-31 10:00", "2026-07-31 10:00", "2026-08-31 10:00", "2026-10-31 10:00"]


def test_until_is_inclusive_and_count_limits():
    e = ev(datetime(2026, 12, 1, 18, 0), "FREQ=DAILY;UNTIL=20261203T235959")
    assert len(rec.expand(e, *window("2026-11-30", "2026-12-31"))) == 3
    e = ev(datetime(2026, 12, 1, 18, 0), "FREQ=WEEKLY;COUNT=3")
    assert len(rec.expand(e, *window("2026-12-01", "2027-12-31"))) == 3


def test_exdates_are_skipped():
    e = ev(datetime(2026, 11, 2, 8, 0), "FREQ=WEEKLY;BYDAY=MO", exdates=["20261109T0800"])
    assert starts(rec.expand(e, *window("2026-11-01", "2026-11-20"))) == ["2026-11-02 08:00", "2026-11-16 08:00"]


def test_all_day_multi_day_series_uses_calendar_days():
    s = datetime(2026, 10, 24, 0, 0).replace(tzinfo=BE).astimezone(UTC)
    e = SimpleNamespace(start_at=s, end_at=datetime(2026, 10, 27, 0, 0).replace(tzinfo=BE).astimezone(UTC), all_day=True, tzid="Europe/Berlin",
                        rrule="FREQ=WEEKLY", exdates=[])
    got = rec.expand(e, *window("2026-10-30", "2026-11-12"))
    # die Dauer ist 3 Kalendertage, auch wenn dazwischen die Uhr zurückgestellt wurde (72 vs 73 Stunden)
    assert [(o.end.astimezone(BE).date() - o.start.astimezone(BE).date()).days for o in got] == [3, 3]


def test_window_touching_event_that_started_before():
    e = ev(datetime(2026, 11, 5, 23, 0), "FREQ=DAILY", minutes=180)
    got = rec.expand(e, *window("2026-11-07 00:00", "2026-11-07 01:00"))
    assert starts(got) == ["2026-11-06 23:00"]  # läuft bis 02:00 in den 7. hinein


def test_series_end():
    s = datetime(2026, 11, 2, 8, 0).replace(tzinfo=BE).astimezone(UTC)
    assert rec.series_end("FREQ=WEEKLY", s, s, "Europe/Berlin") is None
    end = rec.series_end("FREQ=DAILY;COUNT=3", s, s + __import__("datetime").timedelta(hours=1), "Europe/Berlin")
    assert end.astimezone(BE).strftime("%Y-%m-%d %H:%M") == "2026-11-04 09:00"
    end = rec.series_end("FREQ=DAILY;UNTIL=20261110T235959", s, s + __import__("datetime").timedelta(hours=1), "Europe/Berlin")
    assert end.astimezone(BE).strftime("%Y-%m-%d %H:%M") == "2026-11-10 09:00"


def test_cut_until_and_remaining_count():
    s = datetime(2026, 11, 2, 8, 0).replace(tzinfo=BE).astimezone(UTC)
    old, new = rec.cut_until("FREQ=WEEKLY;COUNT=5", s, "Europe/Berlin", datetime(2026, 11, 16, 8, 0))
    assert "COUNT" not in old and old.endswith("UNTIL=20261116T075959")
    assert new == "FREQ=WEEKLY;COUNT=3"  # 5 insgesamt, 2 davor


def test_normalize_until_variants():
    assert rec.normalize("FREQ=DAILY;UNTIL=20261220") == "FREQ=DAILY;UNTIL=20261220T235959"
    assert rec.normalize("RRULE:FREQ=DAILY;UNTIL=20261220T225959Z") == "FREQ=DAILY;UNTIL=20261220T235959"  # 22:59:59 UTC = 23:59:59 MEZ
    with pytest.raises(Invalid):
        rec.normalize("FREQ=SECONDLY")
    with pytest.raises(Invalid):
        rec.normalize("FREQ=DAILY;COUNT=999999")


def test_build_rrule_from_simple_fields():
    assert rec.build_rrule("weekly", 1, ["mo", "mi"]) == "FREQ=WEEKLY;BYDAY=MO,WE"
    assert rec.build_rrule("weekdays") == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    assert rec.build_rrule("monthly", 1, ["di"], nth=2) == "FREQ=MONTHLY;BYDAY=2TU"
    assert rec.build_rrule("daily", 2, until="2026-12-20") == "FREQ=DAILY;INTERVAL=2;UNTIL=20261220T235959"
    assert rec.build_rrule("none") == ""
    with pytest.raises(Invalid):
        rec.build_rrule("hourly")


def test_describe_in_german():
    assert rec.describe("FREQ=DAILY") == "Täglich"
    assert rec.describe("FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261220T235959").startswith("Jeden Mo und Mi bis 20. Dez.")
    assert rec.describe("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR") == "Werktags"
    assert rec.describe("FREQ=MONTHLY;BYDAY=2TU") == "Monatlich am 2. Di"
    assert rec.describe("FREQ=WEEKLY;INTERVAL=2;BYDAY=FR") == "Alle 2 Wochen am Fr"
    assert rec.describe("FREQ=YEARLY;COUNT=3") == "Jährlich, 3 Mal"


def test_occurrence_ids():
    assert rec.split_id("abc") == ("abc", None)
    assert rec.split_id("abc_20261029T1500") == ("abc", "20261029T1500")
    with pytest.raises(Invalid):
        rec.split_id("abc_nonsense")
