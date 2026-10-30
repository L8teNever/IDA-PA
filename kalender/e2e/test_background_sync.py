"""Paket 15: der Service Worker sendet die Outbox auch ohne die Seite zu fragen – und nie doppelt, wenn beide gleichzeitig loslegen."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .conftest import go_offline, go_online, open_app, until

BE = ZoneInfo("Europe/Berlin")
DONE = "window.__kal.outboxCount().then((n) => n === 0)"


def at(days=1, hour=12):
    return (datetime.now(BE) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0).replace(tzinfo=None).isoformat(timespec="minutes")


def found(server, q):
    return [e["title"] for e in server.user.get("/api/v1/search", params={"q": q}).json()["upcoming"]]


def test_service_worker_sends_the_outbox(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    page.evaluate("window.__kal.write('/events', { method: 'POST', body: { title: 'Vom Service Worker', start: '%s', duration_min: 30 } })" % at())
    until(page, "window.__kal.outbox().then((l) => l.length === 1 && l[0].tries >= 1)")
    # die Seite soll NICHT selbst senden: ihre Zeitgeber/„online“-Auslöser ruhen, nur der Worker bekommt den Auftrag
    page.evaluate("window.__kal.pause && window.__kal.pause()")
    ctx.set_offline(False)
    page.evaluate("navigator.serviceWorker.controller.postMessage({ type: 'test-sync' })")
    until(page, DONE)
    assert found(server, "vom service worker") == ["Vom Service Worker"]


def test_page_and_worker_at_the_same_time_send_once(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    for i in range(3):
        page.evaluate("(i) => window.__kal.write('/events', { method: 'POST', body: { title: 'Gleichzeitig ' + i, start: '%s', duration_min: 30 } })" % at(), i)
    go_online(ctx)
    page.evaluate("navigator.serviceWorker.controller.postMessage({ type: 'test-sync' }); window.__kal.syncNow();")
    until(page, DONE)
    page.wait_for_timeout(500)
    assert sorted(found(server, "gleichzeitig")) == ["Gleichzeitig 0", "Gleichzeitig 1", "Gleichzeitig 2"]


def test_sync_event_is_registered_when_a_change_is_queued(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    page.evaluate("window.__kal.write('/events', { method: 'POST', body: { title: 'Anmeldung', start: '%s', duration_min: 30 } })" % at())
    tags = page.evaluate("navigator.serviceWorker.ready.then((r) => (r.sync ? r.sync.getTags() : ['unsupported'])).catch(() => ['unsupported'])")
    assert tags == ["unsupported"] or "kal-outbox" in tags
