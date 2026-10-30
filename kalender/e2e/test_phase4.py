"""Phase 4: Anhänge offline, Erinnerungen ohne Server, Hinweise „braucht Internet“, Schnelleingabe ohne Verbindung."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .conftest import go_offline, go_online, open_app, until

BE = ZoneInfo("Europe/Berlin")
DONE = "window.__kal.outboxCount().then((n) => n === 0)"
PDF = {"name": "Rezept.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 offline"}


def files_of(server, title):
    ev = next(e for e in server.user.get("/api/v1/search", params={"q": title}).json()["upcoming"])
    return ev, server.user.get(f"/api/v1/events/{ev['id']}/files").json()["files"]


def test_attachment_added_to_a_new_event_offline_arrives_after_the_event(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    page.click("#fab")
    page.fill("#fTitle", "Mit Rezept")
    page.click("#fMore")
    page.set_input_files("#fFile", PDF)
    page.wait_for_selector(".pc.file >> text=Rezept.pdf")
    page.click("[data-save]")
    until(page, "window.__kal.outboxCount().then((n) => n === 2)")           # Termin + Anhang warten
    go_online(ctx)
    until(page, DONE)
    ev, files = files_of(server, "mit rezept")
    assert [f["name"] for f in files] == ["Rezept.pdf"]
    assert server.user.get(f"/api/v1/files/{ev['event_id']}/Rezept.pdf").content == b"%PDF-1.4 offline"


def test_attachment_to_an_existing_event_offline_and_no_second_copy_after_a_retry(server, ctx, page):
    start = (datetime.now(BE) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0).replace(tzinfo=None).isoformat(timespec="minutes")
    ev = server.user.post("/api/v1/events", json={"title": "Schon da", "start": start, "duration_min": 60}).json()["event"]
    open_app(page)
    page.evaluate("(id) => window.__kal.write('/events/' + id, { method: 'PATCH', body: { location: 'x' } })", ev["id"])
    until(page, DONE)
    go_offline(ctx)
    page.goto(f"/termin/{ev['id']}")
    page.wait_for_selector(".ev-sheet [data-edit]")
    page.click(".ev-sheet [data-edit]")
    page.click("#fMore")
    page.set_input_files("#fFile", PDF)
    page.wait_for_selector(".pc.file >> text=Rezept.pdf")
    assert page.evaluate("window.__kal.outboxCount()") == 1
    go_online(ctx)
    until(page, DONE)
    assert [f["name"] for f in files_of(server, "schon da")[1]] == ["Rezept.pdf"]
    page.evaluate("window.__kal.syncNow()")
    assert len(files_of(server, "schon da")[1]) == 1


def test_a_local_alarm_rings_offline_when_it_is_due(server, ctx, page):
    soon = (datetime.now(BE) + timedelta(minutes=10)).replace(second=0, microsecond=0).replace(tzinfo=None).isoformat(timespec="minutes")
    server.user.post("/api/v1/events", json={"title": "Gleich dran", "start": soon, "duration_min": 30, "alarms": [10]})
    open_app(page)
    go_offline(ctx)
    page.evaluate("window.__kal.localAlarms()")
    page.wait_for_selector(".alarm h2:text-is('Gleich dran')")
    page.evaluate("window.__kal.localAlarms()")
    assert page.locator(".alarm h2:text-is('Gleich dran')").count() == 1, "nicht zweimal klingeln"


def test_internet_only_features_are_greyed_out_offline(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    page.click("#menu")
    page.click("#dSet")
    page.wait_for_selector("#sBak")
    assert "net-off" in page.get_attribute("#sBak", "class")
    assert "braucht Internet" in page.inner_text("#sBak")
    go_online(ctx)


def test_quick_add_offline_takes_the_text_as_title_and_opens_the_editor(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    page.click("#fab", delay=800)                                   # lange drücken öffnet das Plus-Menü
    page.click("#fabMenu button:has-text('Schnelleingabe')")
    page.fill("#qIn", "Zahnarzt morgen 15 Uhr")
    page.wait_for_selector("text=Offline")
    page.click("#qEdit")
    page.wait_for_selector("#fTitle")
    assert page.input_value("#fTitle") == "Zahnarzt morgen 15 Uhr"
    go_online(ctx)
