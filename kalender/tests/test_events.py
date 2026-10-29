"""Termine über die API: anlegen, Scopes, Papierkorb, Rückgängig, Verlauf."""
from .conftest import API, day, mk, rng


def test_create_read_update_delete(client):
    r = mk(client, "Zahnarzt", "2026-11-05T15:00", end="2026-11-05T16:00", location="Praxis Müller", alarms=[30])
    ev = r["event"]
    assert ev["start"] == "2026-11-05T15:00:00+01:00" and ev["end"] == "2026-11-05T16:00:00+01:00"
    assert ev["alarms"] == [30] and r["change_id"]
    got = day(client, "2026-11-05")
    assert [e["title"] for e in got] == ["Zahnarzt"]
    # verschieben: die Dauer bleibt
    r = client.patch(f"{API}/events/{ev['id']}", json={"start": "2026-11-06T09:00"}).json()
    assert r["event"]["start"].startswith("2026-11-06T09:00") and r["event"]["end"].startswith("2026-11-06T10:00")
    assert day(client, "2026-11-05") == [] and len(day(client, "2026-11-06")) == 1
    client.delete(f"{API}/events/{ev['id']}")
    assert day(client, "2026-11-06") == []


def test_validation_errors(client):
    for body in ({"title": "", "start": "2026-11-05T15:00"}, {"title": "x"}, {"title": "x", "start": "2026-11-05T15:00", "end": "2026-11-05T14:00"},
                 {"title": "x", "start": "kaputt"}, {"title": "x", "start": "2026-11-05T15:00", "alarms": ["a"]},
                 {"title": "x", "start": "2026-11-05T15:00", "color": "neonpink"}, {"title": "x", "start": "2026-11-05"}  # nur Datum → ganztägig, ok
                 ):
        r = client.post(f"{API}/events", json=body)
        if body.get("start") == "2026-11-05" and body.get("title") == "x":
            assert r.status_code == 200  # reines Datum = ganztägig
            client.delete(f"{API}/events/{r.json()['event']['id']}")
        else:
            assert r.status_code == 400, body
            assert "detail" in r.json()
    assert client.get(f"{API}/events/gibtsnicht").status_code == 404


def test_all_day_multi_day_end_is_exclusive(client):
    ev = mk(client, "Urlaub", "2026-12-24", end="2026-12-28", all_day=True)["event"]
    assert ev["all_day"] and ev["start"] == "2026-12-24" and ev["end"] == "2026-12-28"
    # in der Bereichsabfrage taucht er an jedem der 4 Tage auf, am 28. nicht mehr
    assert len(day(client, "2026-12-27")) == 1 and day(client, "2026-12-28") == []
    client.delete(f"{API}/events/{ev['id']}")


def test_event_across_midnight_and_month_end(client):
    ev = mk(client, "Nachtschicht", "2026-11-30T22:00", end="2026-12-01T06:00")["event"]
    assert [e["title"] for e in day(client, "2026-11-30")] == ["Nachtschicht"]
    assert [e["title"] for e in day(client, "2026-12-01")] == ["Nachtschicht"]
    assert day(client, "2026-12-02") == []
    client.delete(f"{API}/events/{ev['id']}")


def test_series_scopes(client):
    ev = mk(client, "Mathe", "2026-11-02T08:00", end="2026-11-02T09:30", rrule="FREQ=WEEKLY;BYDAY=MO;UNTIL=20261231", location="R12")["event"]
    eid = ev["event_id"]
    mathe = lambda: [e for e in rng(client, "2026-11-01", "2026-11-30") if e["title"].startswith("Mathe")]  # noqa: E731
    assert [e["start"][:10] for e in mathe()] == ["2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23", "2026-11-30"]
    # nur dieser Termin verschoben
    r = client.patch(f"{API}/events/{eid}_20261109T0800", params={"scope": "this"}, json={"start": "2026-11-09T10:00", "location": "R14"})
    assert r.status_code == 200, r.text
    got = {e["start"][:16]: e for e in mathe()}
    assert "2026-11-09T10:00" in got and "2026-11-09T08:00" not in got
    assert got["2026-11-09T10:00"]["location"] == "R14" and got["2026-11-16T08:00"]["location"] == "R12"
    # dieser und folgende: ab 16.11. um 09:00
    r = client.patch(f"{API}/events/{eid}_20261116T0800", params={"scope": "following"}, json={"start": "2026-11-16T09:00", "title": "Mathe neu"})
    assert r.status_code == 200, r.text
    got = {e["start"][:16]: e["title"] for e in mathe()}
    assert got["2026-11-02T08:00"] == "Mathe" and got["2026-11-09T10:00"] == "Mathe"
    assert got["2026-11-16T09:00"] == "Mathe neu" and got["2026-11-30T09:00"] == "Mathe neu"
    assert "2026-11-16T08:00" not in got
    # alle: Titel ändern (die alte Serie bis zum 9.)
    client.patch(f"{API}/events/{eid}", json={"title": "Mathe alt"})
    got = {e["start"][:16]: e["title"] for e in mathe()}
    assert got["2026-11-02T08:00"] == "Mathe alt" and got["2026-11-16T09:00"] == "Mathe neu"
    # einen Termin löschen
    client.delete(f"{API}/events/{eid}_20261102T0800", params={"scope": "this"})
    assert "2026-11-02T08:00" not in {e["start"][:16] for e in mathe()}


def test_move_whole_series_by_dragging_one_occurrence(client):
    ev = mk(client, "Chor", "2026-11-04T19:00", end="2026-11-04T20:30", rrule="FREQ=WEEKLY;COUNT=10")["event"]
    # den Termin am 11.11. um 1 Stunde später → die ganze Serie rückt um 1 Stunde
    client.patch(f"{API}/events/{ev['event_id']}_20261111T1900", params={"scope": "all"}, json={"start": "2026-11-11T20:00"})
    got = [e["start"][:16] for e in rng(client, "2026-11-01", "2026-11-20") if e["title"] == "Chor"]
    assert got == ["2026-11-04T20:00", "2026-11-11T20:00", "2026-11-18T20:00"]
    assert [e for e in rng(client, "2026-11-04", "2026-11-04") if e["title"] == "Chor"][0]["end"].startswith("2026-11-04T21:30")


def test_following_delete_ends_series(client):
    ev = mk(client, "Yoga", "2026-11-03T18:00", rrule="FREQ=WEEKLY;UNTIL=20261231")["event"]
    client.delete(f"{API}/events/{ev['event_id']}_20261117T1800", params={"scope": "following"})
    assert [e["start"][:10] for e in rng(client, "2026-11-01", "2027-01-31") if e["title"] == "Yoga"] == ["2026-11-03", "2026-11-10"]


def test_trash_restore_and_undo(client):
    ev = mk(client, "Papierkorb-Test", "2027-02-01T10:00")["event"]
    r = client.delete(f"{API}/events/{ev['id']}").json()
    assert day(client, "2027-02-01") == []
    trash = client.get(f"{API}/trash").json()["events"]
    assert any(e["event_id"] == ev["id"] for e in trash)
    assert client.post(f"{API}/events/{ev['id']}/restore").status_code == 200
    assert [e["title"] for e in day(client, "2027-02-01")] == ["Papierkorb-Test"]
    # Rückgängig der Löschung (nachträglich) und der Anlage
    client.delete(f"{API}/events/{ev['id']}")
    u = client.post(f"{API}/changes/{r['change_id'] + 1}/undo")
    assert u.status_code in (200, 400)
    client.post(f"{API}/events/{ev['id']}/restore")


def test_undo_update_and_redo(client):
    ev = mk(client, "Vorher", "2027-02-02T10:00")["event"]
    ch = client.patch(f"{API}/events/{ev['id']}", json={"title": "Nachher", "location": "Ort"}).json()["change_id"]
    assert day(client, "2027-02-02")[0]["title"] == "Nachher"
    u = client.post(f"{API}/changes/{ch}/undo").json()
    assert day(client, "2027-02-02")[0]["title"] == "Vorher" and u["ok"]
    assert client.post(f"{API}/changes/{ch}/undo").status_code == 400  # schon rückgängig
    # „Wiederholen“ = die Rückgängig-Änderung selbst rückgängig machen
    client.post(f"{API}/changes/{u['change_id']}/undo")
    assert day(client, "2027-02-02")[0]["title"] == "Nachher"
    # Rückgängig einer Anlage legt den Termin in den Papierkorb
    created = mk(client, "Nur kurz", "2027-02-03T10:00")
    client.post(f"{API}/changes/{created['change_id']}/undo")
    assert day(client, "2027-02-03") == []


def test_undo_of_series_split_reverts_both_parts(client):
    ev = mk(client, "Split", "2027-03-01T10:00", rrule="FREQ=DAILY;COUNT=6")["event"]
    r = client.patch(f"{API}/events/{ev['event_id']}_20270304T1000", params={"scope": "following"}, json={"title": "Split B"}).json()
    titles = [e["title"] for e in rng(client, "2027-03-01", "2027-03-10") if e["title"].startswith("Split")]
    assert titles == ["Split"] * 3 + ["Split B"] * 3
    client.post(f"{API}/changes/{r['change_id']}/undo")
    assert [e["title"] for e in rng(client, "2027-03-01", "2027-03-10") if e["title"].startswith("Split")] == ["Split"] * 6


def test_history_records_who_and_how(client, agent):
    ida = agent("ida")
    ev = ida.post(f"{API}/events", json={"title": "Von Ida", "start": "2027-04-01T09:00"}).json()["event"]
    assert ev["created_by"] == "ida"
    ch = client.get(f"{API}/changes", params={"by": "ida"}).json()["changes"]
    assert ch[0]["title"] == "Von Ida" and ch[0]["by"] == "ida" and ch[0]["via"] == "ida" and ch[0]["action"] == "create"
    ida.close()


def test_conflicts_are_reported_but_not_blocking(client):
    mk(client, "Erster", "2027-05-03T10:00", end="2027-05-03T11:00")
    r = mk(client, "Zweiter", "2027-05-03T10:30", end="2027-05-03T11:30")
    assert [c["title"] for c in r["conflicts"]] == ["Erster"]
    assert len(day(client, "2027-05-03")) == 2


def test_default_alarms_from_calendar_and_colors(client):
    cals = client.get(f"{API}/calendars").json()["calendars"]
    priv = next(c for c in cals if c["is_default"])
    client.patch(f"{API}/calendars/{priv['id']}", json={"default_alarms": [15]})
    ev = mk(client, "Mit Standard", "2027-06-01T10:00")["event"]
    assert ev["alarms"] == [15] and ev["color"] == priv["color"]
    ev2 = mk(client, "Ohne", "2027-06-01T12:00", alarms=[])["event"]
    assert ev2["alarms"] == []
    ev3 = mk(client, "Rot", "2027-06-01T13:00", color="rot")["event"]
    assert ev3["color"] == "rot"
    client.patch(f"{API}/calendars/{priv['id']}", json={"default_alarms": []})


def test_dst_day_has_correct_length(client):
    # 25.10.2026: Uhr wird zurückgestellt, der Tag hat 25 Stunden – ein Termin um 23:30 gehört trotzdem zu diesem Tag
    mk(client, "Spät", "2026-10-25T23:30", end="2026-10-25T23:45")
    assert [e["title"] for e in day(client, "2026-10-25") if e["title"] == "Spät"] == ["Spät"]
    assert [e for e in day(client, "2026-10-26") if e["title"] == "Spät"] == []
