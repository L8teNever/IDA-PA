"""Bereichsabfrage, freie Zeit, Konflikte, Tagesübersicht, Feiertage, Statistik."""
from .conftest import API, day, mk


def test_free_time_respects_busy_travel_and_window(client):
    mk(client, "A", "2028-03-06T09:00", end="2028-03-06T10:00")
    mk(client, "B", "2028-03-06T12:00", end="2028-03-06T13:00", travel_min=30)
    mk(client, "Ganztag", "2028-03-06", end="2028-03-07", all_day=True)      # blockiert nicht
    mk(client, "Frei", "2028-03-06T14:00", end="2028-03-06T15:00", transparency="free")  # blockiert nicht
    r = client.get(f"{API}/freebusy", params={"from": "2028-03-06", "to": "2028-03-06", "duration": 60, "within": "08:00-16:00"}).json()["slots"]
    spans = [(s["start"][11:16], s["end"][11:16]) for s in r]
    # 08–09 frei, 10–11:30 (B beginnt mit 30 Min. Reisezeit um 11:30), 13–16
    assert spans == [("08:00", "09:00"), ("10:00", "11:30"), ("13:00", "16:00")]
    r = client.get(f"{API}/freebusy", params={"from": "2028-03-06", "to": "2028-03-06", "duration": 120, "within": "08:00-16:00"}).json()["slots"]
    assert [(s["start"][11:16], s["end"][11:16]) for s in r] == [("13:00", "16:00")]


def test_free_time_weekday_filter_and_limits(client):
    r = client.get(f"{API}/freebusy", params={"from": "2028-06-05", "to": "2028-06-11", "duration": 30, "days": "0"}).json()["slots"]
    assert r and all(s["start"][:10] == "2028-06-05" for s in r)  # nur der Montag
    assert client.get(f"{API}/freebusy", params={"from": "2028-01-01", "to": "2028-12-31"}).status_code == 400  # zu lang


def test_conflicts_endpoint(client):
    mk(client, "Besetzt", "2028-04-03T10:00", end="2028-04-03T11:00")
    r = client.get(f"{API}/conflicts", params={"start": "2028-04-03T10:30", "end": "2028-04-03T11:30"}).json()
    assert not r["free"] and r["conflicts"][0]["title"] == "Besetzt"
    r = client.get(f"{API}/conflicts", params={"start": "2028-04-03T11:00", "end": "2028-04-03T12:00"}).json()
    assert r["free"]  # Ende = Anfang berührt nicht


def test_day_brief(client):
    mk(client, "Morgens", "2028-05-02T08:00", end="2028-05-02T09:00", location="Büro")
    mk(client, "Geburtstag", "2028-05-02", all_day=True)
    r = client.get(f"{API}/brief", params={"date": "2028-05-02"}).json()
    assert r["anzahl"] == 2 and "08:00–09:00 Morgens (Büro)" in r["uebersicht"] and "ganztägig: Geburtstag" in r["uebersicht"]
    assert r["wochentag"] == "Dienstag"


def test_holidays_are_computed_locally(client):
    r = client.get(f"{API}/events", params={"from": "2027-12-24", "to": "2027-12-26"}).json()["events"]
    hol = {e["title"]: e for e in r if e.get("holiday")}
    assert "1. Weihnachtstag" in " ".join(hol) or any("Weihnacht" in t for t in hol)
    e = next(iter(hol.values()))
    assert e["read_only"] and e["all_day"] and e["id"].startswith("hol_")
    # Tag der Deutschen Einheit gilt überall, Fronleichnam nur in NW (nicht in Berlin)
    r = client.get(f"{API}/events", params={"from": "2027-10-03", "to": "2027-10-03"}).json()["events"]
    assert any(e.get("holiday") for e in r)
    # Schreiben in den Feiertags-Kalender geht nicht
    cal = next(c for c in client.get(f"{API}/calendars").json()["calendars"] if c["kind"] == "holidays")
    assert client.post(f"{API}/events", json={"title": "x", "start": "2028-01-01T10:00", "calendar_id": cal["id"]}).status_code == 403


def test_stats(client):
    mk(client, "Lernen", "2028-07-03T10:00", end="2028-07-03T12:00")
    mk(client, "Lernen", "2028-07-04T10:00", end="2028-07-04T11:00")
    r = client.get(f"{API}/stats", params={"from": "2028-07-03", "to": "2028-07-09", "group_by": "title"}).json()
    row = next(x for x in r["zeilen"] if x["name"] == "Lernen")
    assert row["minuten"] == 180 and row["anzahl"] == 2


def test_range_limits(client):
    assert client.get(f"{API}/events", params={"from": "2020-01-01", "to": "2030-01-01"}).status_code == 400
    assert client.get(f"{API}/events", params={"from": "heute", "to": "morgen"}).status_code == 200
    assert client.get(f"{API}/events", params={"from": "diese woche"}).status_code == 200
    assert client.get(f"{API}/events", params={"from": "unsinn"}).status_code == 400


def test_calendar_filter_and_visibility_param(client):
    cals = client.get(f"{API}/calendars").json()["calendars"]
    ida_cal = next(c for c in cals if c["name"] == "Ida")
    mk(client, "In Ida-Kalender", "2028-08-01T10:00", calendar_id=ida_cal["id"])
    mk(client, "In Privat", "2028-08-01T11:00")
    only = day(client, "2028-08-01", calendars=ida_cal["id"])
    assert [e["title"] for e in only] == ["In Ida-Kalender"]
    by_name = client.post(f"{API}/events", json={"title": "Per Name", "start": "2028-08-01T12:00", "calendar": "ida"}).json()["event"]
    assert by_name["calendar_id"] == ida_cal["id"]
