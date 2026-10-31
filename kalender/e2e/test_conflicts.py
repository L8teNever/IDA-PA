"""Phase 3: Konflikte – verschiedene Felder werden zusammengeführt, dieselben fragen nach, Gelöschtes lässt sich neu anlegen."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .conftest import go_offline, go_online, open_app, until

BE = ZoneInfo("Europe/Berlin")
DONE = "window.__kal.outboxCount().then((n) => n === 0)"


def at(days=1, hour=12, minute=0):
    return (datetime.now(BE) + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0).replace(tzinfo=None).isoformat(timespec="minutes")


def mkev(server, title, start=None, **kw):
    r = server.user.post("/api/v1/events", json={"title": title, "start": start or at(), "duration_min": 60, **kw})
    assert r.status_code == 200, r.text
    return r.json()["event"]


def write(page, method, path, body=None):
    return page.evaluate("([m, p, b]) => window.__kal.write(p, { method: m, body: b })", [method, path, body])


def server_ev(server, eid):
    return server.user.get(f"/api/v1/events/{eid}").json()["event"]


def test_different_fields_are_merged_without_asking(server, ctx, page):
    ev = mkev(server, "Zusammen", location="Alt")
    open_app(page)
    go_offline(ctx)
    write(page, "PATCH", f"/events/{ev['id']}?scope=all", {"title": "Zusammen (meine Änderung)"})
    assert server.api.patch(f"/api/v1/events/{ev['id']}", json={"location": "Raum 7 (von Ida)"}).status_code == 200
    go_online(ctx)
    until(page, DONE)
    got = server_ev(server, ev["id"])
    assert (got["title"], got["location"]) == ("Zusammen (meine Änderung)", "Raum 7 (von Ida)"), "beide Änderungen sind da"
    assert page.locator(".bs").count() == 0 and page.evaluate("window.__kal.outbox().then((l) => l.length)") == 0


def test_same_field_asks_and_my_version_wins_when_chosen(server, ctx, page):
    ev = mkev(server, "Zeit streitig", at(1, 10))
    open_app(page)
    go_offline(ctx)
    write(page, "PATCH", f"/events/{ev['id']}?scope=all", {"start": at(1, 16)})
    server.api.patch(f"/api/v1/events/{ev['id']}", json={"start": at(1, 18)})
    go_online(ctx)
    until(page, "window.__kal.outbox().then((l) => l.length === 1 && l[0].state === 'conflict')")
    until(page, "document.querySelector('#offline').dataset.kind === 'conflict'")
    assert "1 Konflikt" in page.inner_text("#offline")
    page.click("#offline [data-sync]")
    page.click(".li.sy.conflict [data-act=open]")
    page.wait_for_selector("text=Der Termin wurde inzwischen geändert")
    assert page.locator(".cf-row").count() == 1 and "Beginn" in page.inner_text(".cf-row")
    page.click("[data-c=mine]")
    until(page, DONE)
    assert server_ev(server, ev["id"])["start"].startswith(at(1, 16))


def test_same_field_keep_the_server_version(server, ctx, page):
    ev = mkev(server, "Stand behalten", at(1, 10), location="Ort A")
    open_app(page)
    go_offline(ctx)
    write(page, "PATCH", f"/events/{ev['id']}?scope=all", {"location": "Ort B (meiner)"})
    server.api.patch(f"/api/v1/events/{ev['id']}", json={"location": "Ort C (Ida)"})
    go_online(ctx)
    until(page, "window.__kal.outbox().then((l) => l.length === 1 && l[0].state === 'conflict')")
    page.evaluate("window.__kal.outbox().then((l) => window.__kal.resolve(l[0].seq, 'server'))")
    until(page, DONE)
    assert server_ev(server, ev["id"])["location"] == "Ort C (Ida)"


def test_changed_but_deleted_meanwhile_can_be_recreated(server, ctx, page):
    ev = mkev(server, "Wird gelöscht und neu angelegt", at(1, 10), location="Ort")
    open_app(page)
    go_offline(ctx)
    write(page, "PATCH", f"/events/{ev['id']}?scope=all", {"title": "Meine Fassung"})
    assert server.api.delete(f"/api/v1/events/{ev['id']}").status_code == 200
    go_online(ctx)
    until(page, "window.__kal.outbox().then((l) => l.length === 1 && l[0].state === 'conflict' && l[0].gone)")
    page.evaluate("window.__kal.outbox().then((l) => window.__kal.resolve(l[0].seq, 'recreate'))")
    until(page, DONE)
    found = server.user.get("/api/v1/search", params={"q": "meine fassung"}).json()["upcoming"]
    assert len(found) == 1 and found[0]["location"] == "Ort"


def test_delete_offline_but_changed_meanwhile_is_a_conflict_not_a_blind_delete(server, ctx, page):
    ev = mkev(server, "Nicht blind löschen", at(1, 10))
    open_app(page)
    go_offline(ctx)
    write(page, "DELETE", f"/events/{ev['id']}?scope=all")
    server.api.patch(f"/api/v1/events/{ev['id']}", json={"location": "Neu von Ida"})
    go_online(ctx)
    until(page, "window.__kal.outbox().then((l) => l.length === 1 && l[0].state === 'conflict')")
    assert server_ev(server, ev["id"])["deleted"] is False, "der Termin wurde NICHT gelöscht"
    page.evaluate("window.__kal.outbox().then((l) => window.__kal.resolve(l[0].seq, 'mine'))")    # „Meine Änderung“ = trotzdem löschen
    until(page, DONE)
    assert server.user.get("/api/v1/search", params={"q": "nicht blind"}).json()["total"] == 0


def test_discard_from_the_sync_view_removes_entry_and_preview(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    write(page, "POST", "/events", {"title": "Wird verworfen", "start": at(1), "duration_min": 30})
    page.goto("/liste")
    page.wait_for_selector("text=Wird verworfen")
    page.click("#menu")
    page.click("#dSet")                                            # Abgleich liegt in den Einstellungen
    page.click("#sSync")
    page.wait_for_selector("h2:has-text('Abgleich')")
    page.once("dialog", lambda d: d.accept())
    page.click(".li.sy >> [data-act=drop]")
    until(page, DONE)
    page.keyboard.press("Escape")
    page.goto("/liste")
    page.wait_for_selector("#title")
    assert page.locator("#stage >> text=Wird verworfen").count() == 0
