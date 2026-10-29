"""Erinnerungen: genau einmal, auch bei Serien und doppeltem Tick; die Warteschlange liefert nach, wenn IDA nicht erreichbar war.

Die Termine liegen relativ zur echten Uhrzeit (die Warteschlange rechnet mit echten Zeitstempeln), `now` wird dem Tick vorgegeben.
"""
from datetime import datetime, timedelta, timezone

from .conftest import API, TOKEN, mk

M = timedelta(minutes=1)


def base() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def tick(client, now: datetime) -> dict:
    r = client.post(f"{API}/_test/tick", params={"now": now.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def alarms(ida, title):
    return [m for m in ida.received if m.get("title") == title]


def event(client, title, start: datetime, hours=1, **kw):
    return mk(client, title, start.isoformat(), end=(start + timedelta(hours=hours)).isoformat(), **kw)["event"]


def test_alarm_fires_exactly_once_and_reaches_ida(client, ida):
    s = base() + timedelta(hours=3)
    event(client, "Zahnarzt-Alarm", s, alarms=[30], location="Praxis")
    assert tick(client, s - 31 * M)["fired"] == 0                  # zu früh
    t1 = tick(client, s - 29 * M)                                  # der Alarm war für s-30 fällig
    assert t1["fired"] == 1 and t1["sent"] == 1
    assert tick(client, s - 28 * M)["fired"] == 0                  # doppelter Tick löst nichts aus
    got = alarms(ida, "Zahnarzt-Alarm")
    assert len(got) == 1
    a = got[0]
    assert a["type"] == "alarm" and a["minutes"] == 30 and a["location"] == "Praxis" and a["action"] == "notify"
    assert datetime.fromisoformat(a["start"]) == s
    assert ida.headers[-1]["authorization"] == f"Bearer {TOKEN}"


def test_several_alarms_per_event(client, ida):
    s = base() + timedelta(hours=5)
    event(client, "Zwei Alarme", s, alarms=[10, 60])
    assert tick(client, s - 61 * M)["fired"] == 0
    assert tick(client, s - 59 * M)["fired"] == 1
    assert [a["minutes"] for a in alarms(ida, "Zwei Alarme")] == [60]
    assert tick(client, s - 9 * M)["fired"] == 1
    assert sorted(a["minutes"] for a in alarms(ida, "Zwei Alarme")) == [10, 60]


def test_series_fires_once_per_occurrence(client, ida):
    s = base() + timedelta(hours=26)
    ev = event(client, "Serien-Alarm", s, rrule="FREQ=DAILY;COUNT=3", alarms=[15])
    occ = [datetime.fromisoformat(e["start"]) for e in client.get(f"{API}/events", params={
        "from": (s - timedelta(days=1)).isoformat(), "to": (s + timedelta(days=5)).isoformat()}).json()["events"] if e["event_id"] == ev["event_id"]]
    assert len(occ) == 3
    n = 0
    for o in occ:
        n += tick(client, o - 14 * M)["fired"]
        n += tick(client, o - 13 * M)["fired"]                      # derselbe Termin noch einmal
    n += tick(client, occ[-1] + timedelta(days=1))["fired"]         # nach dem Ende der Serie
    assert n == 3 and len(alarms(ida, "Serien-Alarm")) == 3
    assert len({a["occurrence_id"] for a in alarms(ida, "Serien-Alarm")}) == 3


def test_grace_period_after_downtime(client, ida):
    s = base() + timedelta(hours=3)
    event(client, "Knapp verpasst", s, alarms=[10])
    assert tick(client, s - 10 * M + 14 * M)["fired"] == 1         # 14 Minuten zu spät: wird nachgeholt
    s2 = base() + timedelta(hours=10)
    event(client, "Viel zu spät", s2, hours=8, alarms=[10])
    assert tick(client, s2 + timedelta(hours=2))["fired"] == 0     # der Alarm ist über 2 Stunden her


def test_cancelled_and_deleted_do_not_ring(client, ida):
    s = base() + timedelta(hours=4)
    event(client, "Abgesagt", s, alarms=[10], status="cancelled")
    gone = event(client, "Gelöscht", s, alarms=[10])
    client.delete(f"{API}/events/{gone['id']}")
    assert tick(client, s - 9 * M)["fired"] == 0
    assert not alarms(ida, "Abgesagt") and not alarms(ida, "Gelöscht")


def test_agent_alarm_carries_the_task(client, ida):
    s = base() + timedelta(hours=6)
    event(client, "Mit Aufgabe", s, alarms=[60], alarm_action="agent", alarm_task="Wegbeschreibung schicken")
    tick(client, s - 59 * M)
    a = alarms(ida, "Mit Aufgabe")[0]
    assert a["action"] == "agent" and a["task"] == "Wegbeschreibung schicken"


def test_outbox_retries_when_ida_is_down(client, ida):
    s = base() + timedelta(minutes=20)
    ida.fail = True
    try:
        event(client, "Nachgeliefert", s, alarms=[10])
        r = tick(client, s - 9 * M)
        assert r["fired"] == 1 and r["sent"] == 0 and not alarms(ida, "Nachgeliefert")
        assert tick(client, s - 9 * M + timedelta(seconds=10))["sent"] == 0   # Wartezeit zwischen den Versuchen
    finally:
        ida.fail = False
    assert tick(client, s - 7 * M)["sent"] >= 1 and len(alarms(ida, "Nachgeliefert")) == 1
    assert tick(client, s - 5 * M)["sent"] == 0                    # nichts doppelt


def test_outbox_gives_up_after_an_hour(client, ida):
    s = base() + timedelta(minutes=20)
    ida.fail = True
    try:
        event(client, "Verworfen", s, alarms=[10])
        tick(client, s - 9 * M)
        tick(client, base() + timedelta(minutes=90))               # über eine Stunde alt: aufgeben
    finally:
        ida.fail = False
    tick(client, base() + timedelta(minutes=100))
    assert not alarms(ida, "Verworfen")
