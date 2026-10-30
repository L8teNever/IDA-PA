"""Phase 2: offline lesen – blättern, Details, Änderungen von Ida kommen von selbst, nach dem Offline-Gehen wird nachgeholt."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .conftest import go_offline, go_online, open_app, until

BE = ZoneInfo("Europe/Berlin")


def now():
    return datetime.now(BE)


def mkev(server, title, when: datetime, **kw):
    body = {"title": title, "start": when.replace(microsecond=0, tzinfo=None).isoformat(timespec="minutes"), "duration_min": 60, **kw}
    r = server.user.post("/api/v1/events", json=body)
    assert r.status_code == 200, r.text
    return r.json()["event"]


def months_from_now(n: int) -> datetime:
    m = now().month - 1 + n
    return now().replace(year=now().year + m // 12, month=m % 12 + 1, day=15, hour=12, minute=0, second=0)


def text(page):
    return page.inner_text("#stage")


def test_browse_minus_three_to_plus_twelve_months_offline(server, ctx, page):
    far, past = months_from_now(11), months_from_now(-2)
    mkev(server, "Weit voraus", far)
    mkev(server, "Schon vorbei", past)
    open_app(page)
    go_offline(ctx)
    for title, when in (("Weit voraus", far), ("Schon vorbei", past)):
        page.goto(f"/liste/{when.date().isoformat()}")           # Liste ab diesem Tag
        page.wait_for_selector("#title")
        page.wait_for_selector(f"text={title}", timeout=10000)
    page.goto(f"/vollmonat/{far.date().isoformat()}")
    page.wait_for_selector("text=Weit voraus", timeout=10000)
    assert "nicht gespeichert" not in page.inner_text("#offline")


def test_outside_the_stored_period_says_so(server, ctx, page):
    open_app(page)
    go_offline(ctx)
    page.goto("/vollmonat/2031-01-15")
    page.wait_for_selector("#title")
    until(page, "document.querySelector('#offline').textContent.includes('nicht gespeichert')")


def test_change_by_ida_shows_up_without_reload(server, ctx, page):
    ev = mkev(server, "Vorher", now().replace(hour=12, minute=0))
    open_app(page)
    page.wait_for_selector("text=Vorher")
    r = server.api.patch(f"/api/v1/events/{ev['id']}", json={"title": "Nachher"})
    assert r.status_code == 200
    page.wait_for_selector("text=Nachher", timeout=15000)
    until(page, "!document.querySelector('#stage').textContent.includes('Vorher')")


def test_changes_made_while_offline_arrive_when_back_online(server, ctx, page):
    ev = mkev(server, "Vor dem Funkloch", now().replace(hour=12, minute=0))
    open_app(page)
    page.wait_for_selector("text=Vor dem Funkloch")
    go_offline(ctx)
    server.api.patch(f"/api/v1/events/{ev['id']}", json={"title": "Nach dem Funkloch"})
    mkev(server, "Neu im Funkloch", now().replace(hour=14, minute=0))
    page.wait_for_timeout(500)
    assert page.locator("#stage >> text=Vor dem Funkloch").count() == 1, "offline bleibt der alte Stand stehen"
    go_online(ctx)
    page.wait_for_selector("text=Nach dem Funkloch", timeout=15000)
    page.wait_for_selector("text=Neu im Funkloch", timeout=15000)


def test_details_open_offline_from_storage(server, ctx, page):
    seen = mkev(server, "Mit Notiz", now().replace(hour=12, minute=0), description="Wichtige Notiz", location="Raum 7")
    mkev(server, "Nie geöffnet", now().replace(hour=15, minute=0), description="Versteckte Notiz")
    open_app(page)
    page.wait_for_selector("text=Mit Notiz")
    page.click("#stage >> text=Mit Notiz")
    page.wait_for_selector(".ev-sheet >> text=Wichtige Notiz")                 # einmal online geöffnet → Details gemerkt
    page.keyboard.press("Escape")
    until(page, "!document.body.classList.contains('ev-open')")
    go_offline(ctx)
    page.reload()
    page.wait_for_selector("text=Mit Notiz")
    page.click("#stage >> text=Mit Notiz")
    page.wait_for_selector(".ev-sheet >> text=Wichtige Notiz")                 # offline aus dem Speicher
    page.keyboard.press("Escape")
    until(page, "!document.body.classList.contains('ev-open')")
    page.click("#stage >> text=Nie geöffnet")
    page.wait_for_selector(".ev-sheet >> text=Offline")                       # nur das, was die Liste weiß, plus Hinweis
    assert page.locator(".ev-sheet >> text=Versteckte Notiz").count() == 0
    assert page.locator(".ev-sheet >> text=Nie geöffnet").count() >= 1


def test_expired_login_keeps_showing_stored_events(server, ctx, page):
    mkev(server, "Bleibt sichtbar", now().replace(hour=12, minute=0))
    open_app(page)
    page.wait_for_selector("text=Bleibt sichtbar")
    page.route("**/api/v1/**", lambda r: r.fulfill(status=302, headers={"location": "https://example.cloudflareaccess.com/login"}))
    page.reload()
    page.wait_for_selector("text=Bleibt sichtbar")
    until(page, "window.__kal.net() === 'auth'")
