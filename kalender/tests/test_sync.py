"""Abgleich (/sync): erster Stand voll, danach nur Änderungen; Serien, Rechte, Kalender-Änderungen, Cursor zu alt."""
from .conftest import API, mk

A, B = "2034-05-01", "2034-07-01"


def sync(client, since=None, meta_at=None, **kw):
    p = {"from": A, "to": B, **kw}
    if since is not None:
        p["since"] = since
    if meta_at:
        p["meta_at"] = meta_at
    r = client.get(f"{API}/sync", params=p)
    assert r.status_code == 200, r.text
    return r.json()


def titles(r):
    return sorted(u["title"] for u in r["upserts"] if not u.get("holiday"))


def test_first_sync_is_full_with_everything(client):
    mk(client, "Sync A", "2034-05-10T10:00")
    r = sync(client)
    assert r["full"] and "Sync A" in titles(r) and r["cursor"] > 0
    assert r["calendars"] and r["settings"] and r["meta_at"]
    assert r["from"] == A and r["to"] == B
    assert any(u.get("holiday") for u in r["upserts"]), "Feiertage kommen im vollen Stand mit"
    assert all("sequence" in u for u in r["upserts"])


def test_delta_has_only_changes_and_removed(client):
    base = sync(client)
    e1 = mk(client, "Delta neu", "2034-05-11T10:00")["event"]
    r = sync(client, since=base["cursor"], meta_at=base["meta_at"])
    assert not r["full"] and titles(r) == ["Delta neu"] and r["removed_events"] == [] and r["calendars"] is None and r["settings"] is None
    cur = r["cursor"]
    client.patch(f"{API}/events/{e1['id']}", json={"title": "Delta geändert"})
    r = sync(client, since=cur, meta_at=base["meta_at"])
    assert titles(r) == ["Delta geändert"] and r["upserts"][0]["sequence"] == 1
    cur = r["cursor"]
    client.delete(f"{API}/events/{e1['id']}")
    r = sync(client, since=cur, meta_at=base["meta_at"])
    assert r["upserts"] == [] and r["removed_events"] == [e1["event_id"]]
    assert sync(client, since=r["cursor"], meta_at=base["meta_at"])["upserts"] == []     # nichts Neues → leer


def test_series_changes_send_all_occurrences_of_that_event(client):
    base = sync(client)
    s = mk(client, "Sync Serie", "2034-05-02T08:00", rrule="FREQ=WEEKLY;COUNT=4")["event"]
    r = sync(client, since=base["cursor"], meta_at=base["meta_at"])
    assert len([u for u in r["upserts"] if u["event_id"] == s["event_id"]]) == 4
    cur = r["cursor"]
    client.patch(f"{API}/events/{s['event_id']}_20340509T0800", params={"scope": "this"}, json={"start": "2034-05-09T10:00", "title": "Sync Ausnahme"})
    r = sync(client, since=cur, meta_at=base["meta_at"])
    by_event = {}
    for u in r["upserts"]:
        by_event.setdefault(u["event_id"], []).append(u["start"][:16])
    assert len(by_event[s["event_id"]]) == 3, "die Serie hat jetzt 3 reguläre Vorkommen (eines ist die Ausnahme)"
    assert any("2034-05-09T10:00" in v for v in by_event.values()) and "Sync Ausnahme" in titles(r)
    assert r["removed_events"] == []


def test_event_leaving_the_window_is_reported_as_removed(client):
    base = sync(client)
    e = mk(client, "Wandert", "2034-05-20T10:00")["event"]
    cur = sync(client, since=base["cursor"], meta_at=base["meta_at"])["cursor"]
    client.patch(f"{API}/events/{e['id']}", json={"start": "2035-05-20T10:00"})
    r = sync(client, since=cur, meta_at=base["meta_at"])
    assert r["upserts"] == [] and e["event_id"] in r["removed_events"]


def test_old_or_unknown_cursor_gives_full_state(client):
    r0 = sync(client)
    assert sync(client, since=r0["cursor"] + 1000)["full"]            # Cursor aus der Zukunft (Datenbank wurde zurückgesetzt?)
    assert not sync(client, since=r0["cursor"], meta_at=r0["meta_at"])["full"]


def test_changed_calendars_force_a_full_state(client):
    r0 = sync(client)
    cal = next(c for c in client.get(f"{API}/calendars").json()["calendars"] if c["name"] == "Ida")
    client.patch(f"{API}/calendars/{cal['id']}", json={"color": "gruen"})
    r = sync(client, since=r0["cursor"], meta_at=r0["meta_at"])
    assert r["full"] and r["meta_at"] > r0["meta_at"]
    assert next(c for c in r["calendars"] if c["id"] == cal["id"])["color"] == "gruen"
    client.patch(f"{API}/calendars/{cal['id']}", json={"color": "lila"})
    client.put(f"{API}/settings", json={"hide_weekends": True})
    r2 = sync(client, since=r["cursor"], meta_at=r["meta_at"])
    assert r2["full"] and r2["settings"]["hide_weekends"] is True
    client.put(f"{API}/settings", json={"hide_weekends": False})


def test_agent_without_read_right_sees_event_as_removed(client, agent):
    ida = agent("ida")
    cal = next(c for c in client.get(f"{API}/calendars").json()["calendars"] if c["name"] == "Privat")
    e = mk(client, "Nur für mich", "2034-06-03T10:00")["event"]
    base = sync(ida)
    assert "Nur für mich" in titles(base)
    cur = base["cursor"]
    client.patch(f"{API}/calendars/{cal['id']}", json={"agent_access": "none"})
    try:
        r = ida.get(f"{API}/sync", params={"from": A, "to": B, "since": cur, "meta_at": base["meta_at"]}).json()
        assert r["full"]                                              # Kalender geändert → alles neu, ohne den versteckten Termin
        assert "Nur für mich" not in titles(r)
    finally:
        client.patch(f"{API}/calendars/{cal['id']}", json={"agent_access": "write"})
    ida.close()


def test_range_limits_and_defaults(client):
    assert client.get(f"{API}/sync", params={"from": "2030-01-01", "to": "2033-01-01"}).status_code == 400
    r = client.get(f"{API}/sync").json()                                 # ohne Zeitraum: −3 / +12 Monate
    assert r["full"] and r["from"] < r["to"]


def test_sequence_and_meta_api_and_conflict_carries_server_state(client):
    assert client.get(f"{API}/meta").json()["api"] == 2
    e = mk(client, "Sequenz", "2034-06-10T10:00")["event"]
    assert e["sequence"] == 0
    r = client.patch(f"{API}/events/{e['id']}", json={"title": "Sequenz 1", "sequence": 0}).json()
    assert r["event"]["sequence"] == 1
    c = client.patch(f"{API}/events/{e['id']}", json={"title": "Zu spät", "sequence": 0})
    assert c.status_code == 409
    body = c.json()
    assert body["server"]["title"] == "Sequenz 1" and body["server"]["sequence"] == 1 and "detail" in body
    assert client.patch(f"{API}/events/{e['id']}", json={"title": "Sequenz 2", "sequence": 1}).status_code == 200
    occ = client.get(f"{API}/events", params={"from": "2034-06-10", "to": "2034-06-10"}).json()["events"]
    assert [x["sequence"] for x in occ if x["event_id"] == e["event_id"]] == [2]
