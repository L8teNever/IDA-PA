"""App-Updates: der neue Service Worker wartet, bis die Offline-Änderungen gesendet sind – es geht nichts verloren."""
import json

from .conftest import open_app, until, version


def queue_entry(page, title):
    """Eine Änderung, die gerade nicht gesendet werden kann, landet in der Outbox."""
    page.evaluate("""(title) => window.__kal.write('/events', { method: 'POST', body: { title, start: '2031-03-03T10:00', duration_min: 30 } })""", title)


def check_for_update(page):
    page.evaluate("navigator.serviceWorker.getRegistration().then((r) => r.update())")


def new_version(server):
    server.edit("js/store.js", "\n// neue Version\n")
    server.restart()


def toast_text(page):
    return page.inner_text("#toast")


def test_update_offers_reload_but_does_not_switch_by_itself(server, ctx, page):
    open_app(page)
    a = version(page)
    new_version(server)
    check_for_update(page)
    page.wait_for_selector("#toast.show")
    assert "Neue Version da" in toast_text(page)
    assert version(page) == a, "die offene App bleibt auf der alten Version, bis der Nutzer „Neu laden“ wählt"
    assert page.evaluate("navigator.serviceWorker.getRegistration().then((r) => !!r.waiting)")


def test_update_without_pending_changes_reloads_into_new_version(server, ctx, page):
    open_app(page)
    a = version(page)
    new_version(server)
    check_for_update(page)
    page.wait_for_selector("#toast.show")
    page.click("#toast button")
    b = until(page, f"window.__kal && window.__kal.version !== '{a}' && window.__kal.version", what="neue Version aktiv")
    assert b != a
    page.wait_for_selector("#title")


def test_update_waits_for_pending_changes_then_sends_them_first(server, ctx, page):
    open_app(page)
    a = version(page)
    # Die Änderung kann gerade nicht gesendet werden (Server nicht erreichbar), die Seitendateien kommen aber noch.
    page.route("**/api/v1/events", lambda r: r.abort() if r.request.method == "POST" else r.continue_())
    queue_entry(page, "Wartet auf Update")
    assert page.evaluate("window.__kal.outboxCount()") == 1
    new_version(server)
    check_for_update(page)
    page.wait_for_selector("#toast.show")
    page.click("#toast button")
    until(page, "document.querySelector('#toast').textContent.includes('nach dem Abgleich')")
    assert version(page) == a, "mit wartenden Änderungen wird nicht neu geladen"
    assert page.evaluate("window.__kal.outboxCount()") == 1
    assert server.user.get("/api/v1/search", params={"q": "wartet auf update"}).json()["total"] == 0
    # Verbindung wieder da → erst senden, dann aktualisieren
    page.unroute("**/api/v1/events")
    page.evaluate("window.__kal.syncNow()")
    until(page, f"window.__kal && window.__kal.version !== '{a}' && window.__kal.version", what="neue Version nach dem Abgleich")
    assert server.user.get("/api/v1/search", params={"q": "wartet auf update"}).json()["total"] == 1, "die Änderung kam an – nichts ging beim Update verloren"
    assert page.evaluate("window.__kal.outboxCount()") == 0


def test_two_app_versions_stay_cached(server, ctx, page):
    open_app(page)
    a = version(page)
    for _ in range(2):
        cur = version(page)
        new_version(server)
        check_for_update(page)
        page.wait_for_selector("#toast.show")
        page.click("#toast button")
        until(page, f"window.__kal && window.__kal.version !== '{cur}' && window.__kal.version")
        page.wait_for_selector("#title")
    c = version(page)
    keys = sorted(page.evaluate("caches.keys()"))
    assert f"kal-{c}" in keys and f"kal-{a}" not in keys, "die älteste Version ist aufgeräumt"
    assert len([k for k in keys if k.startswith("kal-") and k != "kal-meta"]) == 2


def test_offline_navigation_to_any_path_opens_the_app(server, ctx, page):
    open_app(page)
    ctx.set_offline(True)
    for path in ("/monat/2026-11-05", "/termin/abc123", "/woche"):
        page.goto(path)
        page.wait_for_selector("#title")
        assert page.locator("#stage").count() == 1
