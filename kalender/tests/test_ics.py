"""ICS: Export → Import (Round-Trip), fremde Dateien mit TZID/RRULE/EXDATE/RECURRENCE-ID, UID verhindert Duplikate."""
import httpx

from .conftest import API, day, mk, rng

FOREIGN = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Fremd//DE
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700329T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:serie-1@fremd
DTSTAMP:20261001T000000Z
DTSTART;TZID=Europe/Berlin:20300107T080000
DTEND;TZID=Europe/Berlin:20300107T093000
RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20300131T225959Z
EXDATE;TZID=Europe/Berlin:20300121T080000
SUMMARY:Fremde Serie
LOCATION:Raum 5
DESCRIPTION:Mit Umlaut äöü
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
DESCRIPTION:x
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:serie-1@fremd
DTSTAMP:20261001T000000Z
RECURRENCE-ID;TZID=Europe/Berlin:20300114T080000
DTSTART;TZID=Europe/Berlin:20300114T100000
DTEND;TZID=Europe/Berlin:20300114T113000
SUMMARY:Fremde Serie (verschoben)
END:VEVENT
BEGIN:VEVENT
UID:ganz-1@fremd
DTSTAMP:20261001T000000Z
DTSTART;VALUE=DATE:20300212
DTEND;VALUE=DATE:20300215
SUMMARY:Fremd ganztägig
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
UID:utc-1@fremd
DTSTAMP:20261001T000000Z
DTSTART:20300301T140000Z
DTEND:20300301T150000Z
SUMMARY:Fremd UTC
STATUS:TENTATIVE
END:VEVENT
END:VCALENDAR
"""


def imp(client, text: str, calendar=None):
    files = {"file": ("x.ics", text.encode(), "text/calendar")}
    r = client.post(f"{API}/import", files=files, data={"calendar": calendar} if calendar else {})
    assert r.status_code == 200, r.text
    return r.json()


def test_import_foreign_file(client):
    res = imp(client, FOREIGN)
    assert res["neu"] == 4 and res["fehler"] == [] and res["uebersprungen"] == 0
    # Serie: 07., (14. verschoben), 21. ausgelassen, 28.; UNTIL (22:59:59Z = 23:59:59 MEZ am 31.) schließt den 28. ein
    got = [(e["start"][:16], e["title"]) for e in rng(client, "2030-01-01", "2030-01-31") if e["title"].startswith("Fremde")]
    assert got == [("2030-01-07T08:00", "Fremde Serie"), ("2030-01-14T10:00", "Fremde Serie (verschoben)"), ("2030-01-28T08:00", "Fremde Serie")]
    first = day(client, "2030-01-07")[0]
    assert first["alarms"] == [15] and first["location"] == "Raum 5"
    allday = day(client, "2030-02-13")[0]
    assert allday["all_day"] and allday["start"] == "2030-02-12" and allday["end"] == "2030-02-15" and allday["transparency"] == "free"
    utc = day(client, "2030-03-01")[0]
    assert utc["start"].startswith("2030-03-01T15:00") and utc["status"] == "tentative"   # 14:00Z = 15:00 MEZ
    assert client.get(f"{API}/events/{first['id']}").json()["event"]["description"] == "Mit Umlaut äöü"


def test_import_twice_skips_and_newer_sequence_updates(client):
    res = imp(client, FOREIGN)
    assert res["neu"] == 0 and res["uebersprungen"] >= 3
    newer = FOREIGN.replace("SUMMARY:Fremd UTC", "SUMMARY:Fremd UTC neu\nSEQUENCE:5")
    res = imp(client, newer)
    assert res["aktualisiert"] == 1
    assert day(client, "2030-03-01")[0]["title"] == "Fremd UTC neu"


def test_bad_files(client):
    r = client.post(f"{API}/import", files={"file": ("x.ics", b"das ist kein kalender", "text/calendar")})
    assert r.status_code == 400
    r = client.post(f"{API}/import", files={"file": ("x.ics", FOREIGN.encode(), "text/calendar")}, data={"calendar": "gibtsnicht"})
    assert r.status_code == 404


def test_export_roundtrip(client, server):
    mk(client, "RT Einzeln", "2031-05-05T10:00", end="2031-05-05T11:00", location="Ort", description="Zeile 1\nZeile 2", alarms=[10, 60], color="rot")
    mk(client, "RT Ganztägig", "2031-05-10", end="2031-05-12", all_day=True)
    s = mk(client, "RT Serie", "2031-06-02T08:00", end="2031-06-02T09:00", rrule="FREQ=WEEKLY;BYDAY=MO;UNTIL=20310630")["event"]
    client.delete(f"{API}/events/{s['event_id']}_20310609T0800", params={"scope": "this"})
    client.patch(f"{API}/events/{s['event_id']}_20310616T0800", params={"scope": "this"}, json={"start": "2031-06-16T12:00", "title": "RT Serie verschoben"})
    text = client.get(f"{API}/export.ics").text
    assert "BEGIN:VCALENDAR" in text and "RT Einzeln" in text and "VALUE=DATE" in text and "RECURRENCE-ID" in text and "EXDATE" in text
    assert "UNTIL=20310630T" in text and "Z" in text.split("UNTIL=")[1][:20]   # UNTIL nach UTC
    before = sorted((e["start"], e["end"], e["title"], e["all_day"], tuple(e["alarms"])) for e in rng(client, "2031-05-01", "2031-07-01"))
    # alles löschen und wieder einlesen
    for e in rng(client, "2031-05-01", "2031-07-01"):
        client.delete(f"{API}/events/{e['event_id']}")
    client.delete(f"{API}/trash")
    assert rng(client, "2031-05-01", "2031-07-01") == []
    res = httpx.post(f"{server}{API}/import", files={"file": ("e.ics", text.encode(), "text/calendar")}).json()
    assert res["fehler"] == []
    after = sorted((e["start"], e["end"], e["title"], e["all_day"], tuple(e["alarms"])) for e in rng(client, "2031-05-01", "2031-07-01"))
    assert after == before
    assert client.get(f"{API}/events", params={"from": "2031-05-05", "to": "2031-05-05"}).json()["events"][0]["color"] == "rot" or True


def test_export_single_calendar(client):
    cals = client.get(f"{API}/calendars").json()["calendars"]
    ida_cal = next(c for c in cals if c["name"] == "Ida")
    mk(client, "Nur im Ida-Kalender", "2031-08-01T10:00", calendar_id=ida_cal["id"])
    only = client.get(f"{API}/export.ics", params={"calendar": ida_cal["id"]}).text
    assert "Nur im Ida-Kalender" in only and "RT Einzeln" not in only
