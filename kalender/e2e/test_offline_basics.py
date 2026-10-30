"""Phase 1: offline starten, alte Daten übernehmen, Anmeldung ≠ offline."""
import json

from .conftest import go_offline, go_online, open_app, until


def seed_event(server, title="Offline-Test"):
    from datetime import datetime, timedelta
    start = (datetime.now().replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat(timespec="minutes")
    return server.user.post("/api/v1/events", json={"title": title, "start": start, "duration_min": 60}).json()["event"]


def test_offline_reload_still_shows_calendar_and_events(server, ctx, page):
    seed_event(server, "Zahnarzt offline")
    open_app(page)
    go_offline(ctx)
    page.reload()
    page.wait_for_selector("#title")
    until(page, "window.__kal.net() === 'offline'")
    assert "Offline" in page.inner_text("#offline")
    assert page.evaluate("window.__kal.stored()") >= 1
    # der Termin steht auch ohne Netz in der Liste
    page.goto("/liste")                       # auch ein Pfad, den der Server offline nicht kennt, öffnet die App
    page.wait_for_selector("text=Zahnarzt offline", timeout=10000)


def test_schema_1_data_is_taken_over_offline(server, ctx, page):
    """Ein Handy mit der alten App (IndexedDB Schema 1 + Warteschlange in localStorage) bekommt die neue – nichts geht verloren."""
    open_app(page)
    go_offline(ctx)
    # alte Datenbank von Hand herstellen: Version 1, ein Store "kv" mit dem Spiegel
    page.evaluate("""async () => {
      await new Promise((res) => { const r = indexedDB.deleteDatabase('ida-kalender'); r.onsuccess = res; r.onerror = res; });  // onblocked: die App schliesst ihre Verbindung bei versionchange, danach kommt onsuccess
      const d = new Date(); d.setHours(10, 0, 0, 0); const e = new Date(d.getTime() + 3600000);
      const ev = { id: 'alt1', event_id: 'alt1', title: 'Alter Handy-Termin', start: d.toISOString().slice(0,19) + '+00:00', end: e.toISOString().slice(0,19) + '+00:00',
                   all_day: false, calendar_id: 'x', calendar: 'Privat', color: 'blau', location: '', alarms: [], recurring: false, status: 'confirmed', transparency: 'busy', created_by: 'user', updated_by: 'user' };
      const db = await new Promise((res, rej) => { const r = indexedDB.open('ida-kalender', 1); r.onupgradeneeded = () => r.result.createObjectStore('kv'); r.onsuccess = () => res(r.result); r.onerror = rej; });
      await new Promise((res) => { const tx = db.transaction('kv', 'readwrite'); const s = tx.objectStore('kv');
        s.put({ at: Date.now(), from: '2026-01-01', to: '2030-01-01', events: [ev] }, 'window');
        s.put([{ id: 'x', name: 'Privat', color: 'blau', kind: 'normal', visible: true, is_default: true, agent_access: 'write' }], 'cals');
        s.put({ week_start: 1 }, 'settings'); tx.oncomplete = res; });
      db.close();
      localStorage.setItem('kal-queue', JSON.stringify([{ path: '/events', method: 'POST', body: { title: 'Wartet noch', start: '2030-01-01T10:00' } }]));
    }""")
    page.reload()
    page.wait_for_selector("#title")
    until(page, "window.__kal.stored().then(n => n >= 1)")
    assert page.evaluate("window.__kal.outboxCount()") == 1, "die wartende Änderung ist nicht verloren gegangen"
    page.wait_for_selector("text=Alter Handy-Termin", timeout=10000)
    assert "1 Änderung" in page.inner_text("#offline")


def test_expired_login_is_not_shown_as_offline(server, ctx, page):
    """Cloudflare Access leitet bei abgelaufener Sitzung um: das ist „Anmeldung abgelaufen“, nicht „offline“."""
    open_app(page)
    page.route("**/api/v1/**", lambda r: r.fulfill(status=302, headers={"location": "https://example.cloudflareaccess.com/cdn-cgi/access/login"}))
    page.reload()
    page.wait_for_selector("#title")
    until(page, "window.__kal.net() === 'auth'")
    assert "Anmeldung abgelaufen" in page.inner_text("#offline")
    assert "Offline" not in page.inner_text("#offline")
    assert page.locator("body.needs-login").count() == 1 and page.locator("body.is-offline").count() == 0
    # nach erneuter Anmeldung (Route weg) ist alles wieder normal
    page.unroute("**/api/v1/**")
    page.reload()
    until(page, "window.__kal.net() === 'online'")
    assert page.inner_text("#offline") == ""


def test_server_error_is_not_offline(server, ctx, page):
    open_app(page)
    page.route("**/api/v1/events?**", lambda r: r.fulfill(status=502, body="Bad Gateway", content_type="text/html"))
    page.evaluate("window.__kal.syncNow()")
    page.reload()
    page.wait_for_selector("#title")
    assert page.evaluate("window.__kal.net()") != "offline"


def test_settings_show_offline_storage(server, ctx, page):
    open_app(page)
    page.click("#menu")
    page.click("#dData")
    page.wait_for_selector("text=Offline-Speicher")
    txt = page.inner_text("text=Offline-Speicher >> xpath=..")
    assert "dauerhaft" in txt and ("MB" in txt or "KB" in txt)
