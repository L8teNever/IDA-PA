"""Phase 3: offline anlegen, ändern, löschen – sofort sichtbar, später sauber gesendet, nie doppelt, nie verloren."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .conftest import go_offline, go_online, open_app, until

BE = ZoneInfo("Europe/Berlin")
DONE = "window.__kal.outboxCount().then((n) => n === 0)"


def at(days=0, hour=12, minute=0):
    return (datetime.now(BE) + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0).replace(tzinfo=None).isoformat(timespec="minutes")


def mkev(server, title, start=None, **kw):
    r = server.user.post("/api/v1/events", json={"title": title, "start": start or at(1), "duration_min": 60, **kw})
    assert r.status_code == 200, r.text
    return r.json()["event"]


def titles(server, q):
    return [e["title"] for e in server.user.get("/api/v1/search", params={"q": q}).json()["upcoming"]]


def pending(page):
    return page.evaluate("window.__kal.outboxCount()")


def agenda(page):
    page.goto("/liste")
    page.wait_for_selector("#title")


def write(page, method, path, body=None):
    return page.evaluate("([m, p, b]) => window.__kal.write(p, { method: m, body: b })", [method, path, body])


def cloud(page):
    return page.locator("#stage .ms:text-is('cloud_upload')").count()


def test_create_in_the_editor_offline_is_visible_at_once_and_arrives_once(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    page.click("#fab")
    page.fill("#fTitle", "Offline angelegt")
    page.click("[data-save]")
    agenda(page)
    page.wait_for_selector("text=Offline angelegt")
    assert cloud(page) >= 1, "Wolke: noch nicht gespeichert"
    assert pending(page) == 1 and titles(server, "offline angelegt") == []
    go_online(ctx)
    until(page, DONE)
    assert titles(server, "offline angelegt") == ["Offline angelegt"]
    page.wait_for_selector("text=Offline angelegt")
    until(page, "![...document.querySelectorAll('#stage .ms')].some((n) => n.textContent === 'cloud_upload')")


def test_edit_offline_then_online(server, ctx, page):
    ev = mkev(server, "Vor der Änderung")
    open_app(page)
    go_offline(ctx)
    write(page, "PATCH", f"/events/{ev['id']}?scope=all", {"title": "Nach der Änderung", "location": "Neuer Ort"})
    agenda(page)
    page.wait_for_selector("text=Nach der Änderung")
    assert page.locator("#stage >> text=Vor der Änderung").count() == 0
    go_online(ctx)
    until(page, DONE)
    got = server.user.get(f"/api/v1/events/{ev['id']}").json()["event"]
    assert (got["title"], got["location"], got["sequence"]) == ("Nach der Änderung", "Neuer Ort", 1)


def test_delete_offline_hides_at_once_and_deletes_later(server, ctx, page):
    ev = mkev(server, "Wird gelöscht")
    open_app(page)
    go_offline(ctx)
    write(page, "DELETE", f"/events/{ev['id']}?scope=all")
    agenda(page)
    assert page.locator("#stage >> text=Wird gelöscht").count() == 0
    assert titles(server, "wird gelöscht") == ["Wird gelöscht"]
    go_online(ctx)
    until(page, DONE)
    assert titles(server, "wird gelöscht") == []


def test_create_then_delete_offline_never_reaches_the_server(server, ctx, page):
    seen = []
    page.route("**/api/v1/events", lambda r: (seen.append(r.request.method), r.continue_()))
    open_app(page)
    go_offline(ctx)
    write(page, "POST", "/events", {"title": "Flüchtig", "start": at(1), "duration_min": 30})
    until(page, "window.__kal.outbox().then((l) => l[0].state === 'pending' && l[0].tries >= 1)")   # der erste Sendeversuch ist (offline) gescheitert
    target = page.evaluate("window.__kal.outbox().then((l) => l[0].target)")
    write(page, "DELETE", f"/events/{target}?scope=all")
    assert pending(page) == 0, "anlegen + löschen heben sich auf"
    seen.clear()                            # (der erste, offline gescheiterte Versuch zählt nicht)
    go_online(ctx)
    page.evaluate("window.__kal.syncNow()")
    page.wait_for_timeout(500)
    assert "POST" not in seen and "DELETE" not in seen
    assert titles(server, "flüchtig") == []


def test_create_then_move_offline_sends_one_event_with_the_new_time(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    write(page, "POST", "/events", {"title": "Reihenfolge", "start": at(1, 9), "duration_min": 30})
    until(page, "window.__kal.outbox().then((l) => l[0].state === 'pending' && l[0].tries >= 1)")
    target = page.evaluate("window.__kal.outbox().then((l) => l[0].target)")
    write(page, "PATCH", f"/events/{target}?scope=all", {"start": at(2, 14)})
    assert pending(page) == 1, "anlegen + ändern ist ein Eintrag"
    go_online(ctx)
    until(page, DONE)
    r = server.user.get("/api/v1/search", params={"q": "reihenfolge"}).json()["upcoming"]
    assert len(r) == 1 and r[0]["start"].startswith(at(2, 14))


def test_lost_response_does_not_create_a_duplicate(server, ctx, page):
    """Der Server hat gespeichert, die Antwort kommt nicht an → die App sendet erneut → es bleibt EIN Termin."""
    open_app(page)
    state = {"drop": True}

    def handler(route):
        if route.request.method == "POST" and state["drop"]:
            route.fetch()                       # der Server bekommt die Anfrage und speichert
            route.abort("connectionfailed")     # der Browser erfährt es nicht
        else:
            route.continue_()
    page.route("**/api/v1/events", handler)
    write(page, "POST", "/events", {"title": "Nur einmal", "start": at(1), "duration_min": 30})
    until(page, "window.__kal.outboxCount().then((n) => n === 1)")
    assert titles(server, "nur einmal") == ["Nur einmal"], "der Server hat ihn schon"
    state["drop"] = False
    page.unroute("**/api/v1/events")
    page.evaluate("window.__kal.syncNow()")
    until(page, DONE)
    assert titles(server, "nur einmal") == ["Nur einmal"]


def test_login_expired_while_sending_keeps_the_change_and_sends_after_login(server, ctx, page):
    open_app(page)
    page.route("**/api/v1/**", lambda r: r.fulfill(status=302, headers={"location": "https://example.cloudflareaccess.com/login"}))
    write(page, "POST", "/events", {"title": "Nach der Anmeldung", "start": at(1), "duration_min": 30})
    until(page, "window.__kal.net() === 'auth'")
    assert pending(page) == 1
    bar = page.inner_text("#offline")
    assert "Anmeldung abgelaufen" in bar and "1 Änderung" in bar
    page.unroute("**/api/v1/**")
    page.evaluate("window.__kal.syncNow()")
    until(page, DONE)
    assert titles(server, "nach der anmeldung") == ["Nach der Anmeldung"]


def test_changes_survive_closing_the_app_while_offline(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    write(page, "POST", "/events", {"title": "Überlebt Neustart", "start": at(1), "duration_min": 30})
    page.reload()
    page.wait_for_selector("#title")
    assert pending(page) == 1
    agenda(page)
    page.wait_for_selector("text=Überlebt Neustart")
    go_online(ctx)
    until(page, DONE)
    assert titles(server, "überlebt neustart") == ["Überlebt Neustart"]


def test_old_queue_from_the_previous_app_version_is_sent(server, ctx, page):
    open_app(page)
    q = [{"path": "/events", "method": "POST", "body": {"title": "Aus der alten Warteschlange", "start": at(1), "duration_min": 30}}]
    page.evaluate("(q) => localStorage.setItem('kal-queue', q)", json.dumps(q))
    page.reload()
    page.wait_for_selector("#title")
    until(page, DONE)
    assert titles(server, "aus der alten warteschlange") == ["Aus der alten Warteschlange"]
    assert page.evaluate("localStorage.getItem('kal-queue')") is None
