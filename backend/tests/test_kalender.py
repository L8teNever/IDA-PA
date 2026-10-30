"""IDA ↔ Kalender-Dienst: calendar_*-Tools, Webhook für Erinnerungen, Proxy /kal/*, Termine auf der Startseite."""
import json
import threading

import httpx

from .conftest import chat

API = "/api/v1"
TOKEN = "test-kalender-token-123456"


def said(ev) -> str:
    return "".join(e.get("text", "") for e in ev)


def test_ida_creates_an_event_through_the_tool(client, kalender):
    kalender.clear()
    out = said(chat(client, text='TOOL calendar_event_create {"title": "Zahnarzt", "start": "2026-11-05T15:00", "duration_min": 60, "location": "Praxis", "alarms": [30]}'))
    assert '"ok": true' in out and "Zahnarzt" in out and "change_id" in out
    r = kalender.last("/events")
    assert r["method"] == "POST" and r["headers"]["x-kalender-by"] == "ida" and r["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert r["body"]["title"] == "Zahnarzt" and r["body"]["start"] == "2026-11-05T15:00" and r["body"]["alarms"] == [30] and r["body"]["location"] == "Praxis"


def test_the_agent_id_is_passed_on(client, kalender):
    kalender.clear()
    chat(client, agent_id="lea", text='TOOL calendar_event_create {"title": "Von Lea", "start": "2026-11-06T10:00"}')
    assert kalender.last("/events")["headers"]["x-kalender-by"] == "lea"


def test_repeat_fields_become_a_repeat_object_and_all_day_end_is_inclusive(client, kalender):
    kalender.clear()
    chat(client, text='TOOL calendar_event_create {"title": "Mathe", "start": "2026-11-02T08:00", "repeat": "weekly", "days": ["mo", "mi"], "until": "2026-12-20"}')
    assert kalender.last("/events")["body"]["repeat"] == {"freq": "weekly", "interval": 1, "days": [0, 2], "until": "2026-12-20", "count": None, "nth": None}
    chat(client, text='TOOL calendar_event_create {"title": "Urlaub", "start": "2026-12-24", "end": "2026-12-26"}')
    b = kalender.last("/events")["body"]
    assert (b["start"], b["end"]) == ("2026-12-24", "2026-12-27")      # das Tool nimmt den letzten Tag, der Kalender das exklusive Ende
    out = said(chat(client, text='TOOL calendar_event_create {"title": "x", "start": "2026-12-24", "until": "2026-12-26"}'))
    assert "repeat" in out and "error" in out                          # until ohne repeat ist ein Fehler, kein stilles Ignorieren


def test_only_the_main_agent_in_a_live_chat_manages_calendars(client, kalender):
    assert "Keine Berechtigung" in said(chat(client, agent_id="lea", text='TOOL calendar_create {"name": "Heimlich"}'))
    kalender.clear()
    chat(client, text='TOOL calendar_create {"name": "Schule", "color": "gruen"}')
    assert kalender.last("/calendars")["method"] == "POST"


def test_calendar_down_gives_a_clear_error_and_the_chat_continues(client, kalender):
    kalender.clear()
    kalender.mode = "drop"
    try:
        out = said(chat(client, text='TOOL calendar_events {"from": "heute"}'))
        assert "nicht erreichbar" in out
        assert said(chat(client, text="Hallo")).strip() != ""
    finally:
        kalender.mode = "ok"
    kalender.mode = "error"
    try:
        assert "Fehler" in said(chat(client, text='TOOL calendar_events {"from": "heute"}'))
    finally:
        kalender.mode = "ok"


def test_events_tool_formats_for_ida(client, kalender):
    out = said(chat(client, text='TOOL calendar_events {"from": "2026-11-05"}'))
    assert "Fake-Termin" in out and "ganztägig" in out and kalender.last("/events")["query"]["from"] == "2026-11-05"


# ---------------- Webhook ----------------
def hook(client, payload, token=TOKEN):
    return client.post(f"{API}/ext/kalender", json=payload, headers={"Authorization": f"Bearer {token}"} if token else {})


ALARM = {"type": "alarm", "title": "Zahnarzt", "start": "2026-11-05T15:00:00+01:00", "end": "2026-11-05T16:00:00+01:00", "all_day": False, "location": "Praxis", "minutes": 30,
         "action": "notify", "task": ""}


def test_webhook_needs_the_token(client):
    assert hook(client, ALARM, token=None).status_code == 401
    assert hook(client, ALARM, token="falsch").status_code == 401
    assert hook(client, ALARM, token="").status_code == 401


def test_alarm_becomes_a_notice(client):
    assert hook(client, ALARM).status_code == 200
    notices = client.get(f"{API}/home").json()["notices"]
    n = next(x for x in notices if "Zahnarzt" in x["title"])
    assert n["title"].startswith("📅") and "Praxis" in n["text"] and "in 30 Min." in n["text"]
    client.post(f"{API}/messages/read-all")


def test_alarm_with_task_creates_an_agent_task_reminder(client):
    assert hook(client, {**ALARM, "title": "Konzert", "action": "agent", "task": "Wegbeschreibung schicken"}).status_code == 200
    rem = [r for r in client.get(f"{API}/reminders").json() if "Konzert" in r["text"]]
    assert rem and rem[0]["kind"] == "task" and "Wegbeschreibung schicken" in rem[0]["text"]


def test_other_webhook_types_are_ignored(client):
    r = hook(client, {"type": "etwas-anderes"})
    assert r.status_code == 200 and r.json().get("ignored")


# ---------------- Proxy ----------------
def test_proxy_passes_requests_with_the_ida_token_as_user(client, kalender):
    kalender.clear()
    r = client.get("/kal/api/v1/meta")
    assert r.status_code == 200 and r.json()["version"] == "fake1"
    h = kalender.last("/meta")["headers"]
    assert h["x-kalender-by"] == "user" and h["authorization"] == f"Bearer {TOKEN}" and h["x-forwarded-prefix"] == "/kal"
    # ein eigener Authorization-Header der Seite wird nicht weitergereicht
    client.get("/kal/api/v1/meta", headers={"Authorization": "Bearer boese"})
    assert kalender.last("/meta")["headers"]["authorization"] == f"Bearer {TOKEN}"


def test_proxy_forwards_the_idempotency_key_and_bodies(client, kalender):
    kalender.clear()
    r = client.post("/kal/api/v1/events", json={"title": "Via Proxy", "start": "2026-11-07T10:00"}, headers={"Idempotency-Key": "abc123"})
    assert r.status_code == 200 and r.json()["event"]["title"] == "Via Proxy"
    q = kalender.last("/events")
    assert q["headers"]["idempotency-key"] == "abc123" and q["body"]["title"] == "Via Proxy"


def test_proxy_streams_server_sent_events_line_by_line(client, server, kalender):
    got = []
    with httpx.stream("GET", f"{server}/kal/api/v1/stream", timeout=10) as r:
        assert r.status_code == 200 and "text/event-stream" in r.headers["content-type"]
        for line in r.iter_lines():
            if line.startswith("data:"):
                got.append(json.loads(line[5:]))
    assert [g["n"] for g in got] == [0, 1, 2]


def test_proxy_reports_a_missing_calendar_service(client, kalender):
    kalender.mode = "drop"
    try:
        r = client.get("/kal/api/v1/meta")
        assert r.status_code == 503 and "nicht erreichbar" in r.json()["detail"]
    finally:
        kalender.mode = "ok"


def test_spa_fallback_does_not_swallow_the_proxy_prefix(client):
    assert client.get("/kal/gibts-nicht-im-fake").status_code == 404
    assert client.get("/kalender").headers["content-type"].startswith("text/html")


# ---------------- Startseite ----------------
def test_start_page_shows_events_from_the_calendar(client, kalender):
    kalender.clear()
    home = client.get(f"{API}/home").json()
    assert home["calendar"]["on"] and home["calendar"]["local"] and home["calendar"]["error"] is None
    ev = next(x for x in home["timeline"] if x.get("title") == "Fake-Termin")
    assert ev["kind"] == "event" and ev["all_day"] and ev["event_id"] == "ev1" and ev["color"] == "blau"
    assert kalender.last("/events")["query"]["from"] == kalender.last("/events")["query"]["to"]      # genau heute


def test_start_page_survives_a_calendar_outage(client, kalender):
    import app.services.kalender_client as kc
    kc.invalidate()
    kalender.mode = "drop"
    try:
        home = client.get(f"{API}/home").json()
        assert home["calendar"]["error"] and "nicht erreichbar" in home["calendar"]["error"] and isinstance(home["timeline"], list)
    finally:
        kalender.mode = "ok"
        kc.invalidate()


def test_home_calendar_settings_offer_the_local_calendar(client):
    s = client.get(f"{API}/home-calendar").json()
    assert s["local"] is True and s["active"]["source"] == "kalender"
    assert client.put(f"{API}/home-calendar", json={"off": True}).json()["active"] is None
    assert client.put(f"{API}/home-calendar", json={"source": "kalender"}).json()["active"]["source"] == "kalender"
    t = client.post(f"{API}/home-calendar/test", json={"source": "kalender"}).json()
    assert t["tool"] == "IDA Kalender" and t["events"][0]["title"] == "Fake-Termin"
    client.put(f"{API}/home-calendar", json={})                      # zurück auf automatisch
