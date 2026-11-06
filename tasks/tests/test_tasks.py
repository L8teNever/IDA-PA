import hashlib
import hmac
from datetime import date, timedelta

import pytest

from app import days
from app.days import BadDay, parse

from .conftest import SECRET, ida


def ids(client, view):
    return [t["title"] for t in client.get("/api/v1/tasks", params={"view": view}).json()["tasks"]]


def test_create_lands_on_start_page_sorted_by_priority(client):
    client.post("/api/v1/tasks", json={"title": "Wäsche"})
    client.post("/api/v1/tasks", json={"title": "Bewerbung", "priority": 3})
    client.post("/api/v1/tasks", json={"title": "Zimmer", "priority": 1})
    assert ids(client, "heute") == ["Bewerbung", "Zimmer", "Wäsche"]
    assert ids(client, "geplant") == []


def test_plan_moves_task_until_its_day(client):
    t = client.post("/api/v1/tasks", json={"title": "Zahnarzt anrufen"}).json()["task"]
    later = (days.today() + timedelta(days=3)).isoformat()
    r = client.patch(f"/api/v1/tasks/{t['id']}", json={"day": later}).json()["task"]
    assert r["state"] == "geplant" and r["day"] == later
    assert ids(client, "heute") == [] and ids(client, "geplant") == ["Zahnarzt anrufen"]
    # Tag heute oder früher = einfach wieder auf der Startseite
    r = client.patch(f"/api/v1/tasks/{t['id']}", json={"day": "heute"}).json()["task"]
    assert r["day"] is None and r["state"] == "heute"


def test_done_and_restore(client):
    t = client.post("/api/v1/tasks", json={"title": "Müll"}).json()["task"]
    r = client.patch(f"/api/v1/tasks/{t['id']}", json={"done": True}).json()["task"]
    assert r["done"] and r["state"] == "erledigt"
    assert ids(client, "erledigt") == ["Müll"] and ids(client, "heute") == []
    r = client.patch(f"/api/v1/tasks/{t['id']}", json={"done": False}).json()["task"]
    assert not r["done"] and ids(client, "heute") == ["Müll"]
    assert client.get("/api/v1/counts").json() == {"heute": 1, "geplant": 0, "erledigt": 0}


def test_due_overdue_and_range_filter(client):
    y = (days.today() - timedelta(days=1)).isoformat()
    t = client.post("/api/v1/tasks", json={"title": "Referat", "due": y}).json()["task"]
    assert t["overdue"]
    nxt = (days.today() + timedelta(days=10)).isoformat()
    client.post("/api/v1/tasks", json={"title": "Später", "day": nxt})
    got = client.get("/api/v1/tasks", params={"view": "offen", "day_from": nxt, "day_to": nxt}).json()["tasks"]
    assert [x["title"] for x in got] == ["Später"]


def test_search_and_validation(client):
    client.post("/api/v1/tasks", json={"title": "Mathe Hausaufgabe", "note": "Seite 12"})
    client.post("/api/v1/tasks", json={"title": "Einkaufen"})
    assert [t["title"] for t in client.get("/api/v1/tasks", params={"view": "alle", "q": "seite"}).json()["tasks"]] == ["Mathe Hausaufgabe"]
    assert client.post("/api/v1/tasks", json={"title": "x", "day": "irgendwann"}).status_code == 400
    assert client.post("/api/v1/tasks", json={"title": "x", "priority": 4}).status_code == 422
    assert client.patch("/api/v1/tasks/nope", json={"done": True}).status_code == 404


def test_agents_are_recorded_and_bad_tokens_rejected(client):
    t = client.post("/api/v1/tasks", json={"title": "Von Stella"}, headers=ida("stella")).json()["task"]
    assert t["created_by"] == "stella"
    assert client.get("/api/v1/tasks", headers={"authorization": "Bearer falsch"}).status_code == 401


def test_voice_ticket_only_for_the_app(client):
    r = client.post("/api/v1/voice/ticket").json()
    assert r["url"] == "wss://voice.example.com/live" and r["mode"] == "tasks"
    exp, nonce, sig = r["ticket"].split(".")
    assert sig == hmac.new(SECRET.encode(), f"{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()
    assert client.post("/api/v1/voice/ticket", headers=ida("stella")).status_code == 403


def test_app_shell_is_served(client):
    html = client.get("/").text
    assert "IDA Tasks" in html and "/s/" in html
    assert client.get("/manifest.webmanifest").json()["short_name"] == "Tasks"
    assert "__VERSION__" not in client.get("/sw.js").text


@pytest.mark.parametrize("raw,want", [
    ("morgen", 1), ("übermorgen", 2), ("in 3 tagen", 3), ("in einer woche", 7), ("nächste woche", 7 - 2), ("freitag", 2), ("mittwoch", 7),
])
def test_day_words(raw, want):
    base = date(2026, 11, 4)  # ein Mittwoch
    assert parse(raw, base) == base + timedelta(days=want)


def test_day_formats():
    base = date(2026, 11, 4)
    assert parse("12.11.", base) == date(2026, 11, 12)
    assert parse("1.2.", base) == date(2027, 2, 1)  # schon vorbei → nächstes Jahr
    assert parse("2026-12-24", base) == date(2026, 12, 24)
    assert parse("", base) is None
    with pytest.raises(BadDay):
        parse("bald", base)
