"""Suche (FTS5), Schnelleingabe, Backups, Einstellungen, Meta, Live-Stream."""
import sqlite3
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from app import palette
from app.services import parse

from .conftest import API, TOKEN, mk

BE = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 14, 0, tzinfo=BE)   # Donnerstag


# ---------------- Suche ----------------
def titles(res):
    return [v["title"] for v in res["upcoming"]] + [v["title"] for v in res["past"]]


def test_search_umlauts_prefix_and_exclusion(client):
    mk(client, "Zahnarzt Müller", "2032-02-02T10:00", location="Größenwahnstraße 1")
    mk(client, "Zahn Reinigung", "2032-02-03T10:00")
    mk(client, "Elternabend", "2032-02-04T19:00", description="Schule, Raum 12")
    s = lambda q, **kw: client.get(f"{API}/search", params={"q": q, **kw}).json()  # noqa: E731
    assert set(titles(s("zahn"))) >= {"Zahnarzt Müller", "Zahn Reinigung"}      # Präfix
    assert "Zahnarzt Müller" in titles(s("mueller"))                                   # ü = ue
    assert "Zahnarzt Müller" in titles(s("MÜLLER"))
    assert "Zahnarzt Müller" in titles(s("groessenwahnstrasse"))                        # ö = oe, ß = ss
    assert "Zahnarzt Müller" in titles(s("zahn -reinigung")) and "Zahn Reinigung" not in titles(s("zahn -reinigung"))
    assert titles(s('"zahnarzt müller"')) == ["Zahnarzt Müller"]                      # exakte Wortfolge
    assert "Elternabend" in titles(s("schule raum"))                                   # beide Wörter (UND), Beschreibung
    assert s("xyzunbekannt")["total"] == 0
    assert s("")["total"] == 0


def test_search_follows_changes_and_deletions(client):
    ev = mk(client, "Suchkandidat Alpha", "2032-03-01T10:00")["event"]
    assert titles(client.get(f"{API}/search", params={"q": "alpha"}).json()) == ["Suchkandidat Alpha"]
    client.patch(f"{API}/events/{ev['id']}", json={"title": "Suchkandidat Omega"})
    assert client.get(f"{API}/search", params={"q": "alpha"}).json()["total"] == 0
    assert titles(client.get(f"{API}/search", params={"q": "omega"}).json()) == ["Suchkandidat Omega"]
    client.delete(f"{API}/events/{ev['id']}")
    assert client.get(f"{API}/search", params={"q": "omega"}).json()["total"] == 0


def test_search_groups_series_and_range_in_text(client):
    mk(client, "Serie Sucht", "2026-01-05T08:00", rrule="FREQ=WEEKLY;BYDAY=MO")
    r = client.get(f"{API}/search", params={"q": "serie sucht"}).json()
    assert len(r["upcoming"]) == 1 and r["upcoming"][0]["more"] > 40 and r["past"] == []   # nächster Termin + „weitere“
    r = client.get(f"{API}/search", params={"q": "serie sucht diese woche"}).json()
    assert r["range"]["label"] == "Diese woche" and len(r["upcoming"]) <= 1
    old = mk(client, "Alter Termin Q", "2020-01-01T10:00")["event"]
    r = client.get(f"{API}/search", params={"q": "alter termin q"}).json()
    assert [v["id"] for v in r["past"]] == [old["id"]] and r["upcoming"] == []


def test_suggest_titles(client):
    mk(client, "Fahrstunde", "2032-04-01T16:00", end="2032-04-01T17:30", location="Fahrschule", alarms=[30])
    s = client.get(f"{API}/suggest", params={"prefix": "fahr"}).json()["suggestions"]
    assert s[0]["title"] == "Fahrstunde" and s[0]["duration_min"] == 90 and s[0]["location"] == "Fahrschule" and s[0]["alarms"] == [30]
    assert client.get(f"{API}/suggest", params={"prefix": "f"}).json()["suggestions"] == []


# ---------------- Schnelleingabe ----------------
def q(text, **kw):
    return parse.quick(text, now=NOW, calendars=["Privat", "Ida"], **kw)


def test_quick_basic_tomorrow_with_time_duration_place_calendar_alarm():
    r = q("morgen 15 Uhr Zahnarzt 1h @Praxis Müller #privat !30m")
    assert r["title"] == "Zahnarzt" and r["start"] == "2026-10-30T15:00" and r["end"] == "2026-10-30T16:00"
    assert r["location"] == "Praxis Müller" and r["calendar"] == "Privat" and r["alarms"] == [30] and not r["all_day"]
    assert {s["kind"] for s in r["spans"]} >= {"date", "time", "duration", "location", "calendar", "alarm"}


def test_quick_weekday_and_clock_formats():
    assert q("Freitag 8:30 Mathe")["start"] == "2026-10-30T08:30"
    assert q("am Mo 17.30 Uhr Training")["start"] == "2026-11-02T17:30"
    assert q("Sport um 18 Uhr")["start"] == "2026-10-29T18:00"              # heute, 18 Uhr liegt noch vor uns
    assert q("Sport um 9 Uhr")["start"] == "2026-10-30T09:00"               # 9 Uhr ist heute schon vorbei → morgen
    assert q("halb 4 Kaffee")["start"] == "2026-10-29T15:30"                # halb 4 = 15:30; das liegt heute noch vor uns (14:00)
    assert q("12.11. 10 Uhr Arzt")["start"] == "2026-11-12T10:00"
    assert q("in 3 Tagen 11 Uhr Friseur")["start"] == "2026-11-01T11:00"
    r = q("von 8 bis 9:30 Mathe morgen")
    assert r["start"] == "2026-10-30T08:00" and r["end"] == "2026-10-30T09:30" and r["title"] == "Mathe"
    assert q("Meeting 15:00-16:30 morgen")["end"] == "2026-10-30T16:30"
    assert q("Lernen 90 min morgen 10 Uhr")["end"] == "2026-10-30T11:30"
    assert q("Lernen 1,5h morgen 10 Uhr")["end"] == "2026-10-30T11:30"


def test_quick_all_day_and_defaults():
    r = q("Freitag ganztägig Urlaub")
    assert r["all_day"] and r["start"] == "2026-10-30" and r["end"] == "2026-10-31" and r["title"] == "Urlaub"
    assert q("Freitag Urlaub")["all_day"]                                  # nur Datum → ganztägig
    r = q("Zahnarzt")
    assert r["all_day"] and r["start"] == "2026-10-29" and r["title"] == "Zahnarzt"


def test_quick_recurrence():
    r = q("jeden Mo 8:00 Mathe bis 20.12.")
    assert r["rrule"] == "FREQ=WEEKLY;BYDAY=MO;UNTIL=20261220T235959" and r["start"] == "2026-11-02T08:00" and r["title"] == "Mathe"
    assert r["recurrence_text"].startswith("Jeden Mo")
    assert q("täglich 7 Uhr Tabletten")["rrule"] == "FREQ=DAILY"
    assert q("werktags 7:30 Bus")["rrule"] == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    assert q("jeden Di und Do 18 Uhr Chor")["rrule"] == "FREQ=WEEKLY;BYDAY=TU,TH"
    assert q("alle 2 Wochen Freitag 10 Uhr Putzen")["rrule"] == "FREQ=WEEKLY;INTERVAL=2"
    assert q("monatlich 1.12. Miete")["rrule"] == "FREQ=MONTHLY"


def test_quick_does_not_misread_words():
    r = q("Do not disturb Projekt")        # „Do“ ist hier kein Donnerstag
    assert r["title"] == "Do not disturb Projekt"
    assert q("Zimmer 12-14 aufräumen")["title"] == "Zimmer 12-14 aufräumen"


def test_extract_range_for_search():
    rest, rng_, label = parse.extract_range("zahnarzt nächste woche", NOW)
    assert rest == "zahnarzt" and label.lower().startswith("nächste woche")
    assert rng_[0].astimezone(BE).date() == date(2026, 11, 2)
    rest, rng_, _ = parse.extract_range("mathe im november", NOW)
    assert rest == "mathe" and rng_[0].astimezone(BE).date() == date(2026, 11, 1) and rng_[1].astimezone(BE).date() == date(2026, 12, 1)
    assert parse.extract_range("was ist am 12.11.", NOW)[1][0].astimezone(BE).date() == date(2026, 11, 12)
    assert parse.extract_range("nur text", NOW)[1] is None


def test_quick_endpoint(client):
    r = client.post(f"{API}/quick", json={"text": "morgen 15 Uhr Zahnarzt #ida"}).json()
    assert r["title"] == "Zahnarzt" and r["calendar"] == "Ida" and r["spans"]
    assert client.post(f"{API}/quick", json={"text": "  "}).status_code == 400


# ---------------- Backup, Einstellungen, Meta ----------------
def test_backup_creates_valid_database_and_prunes(client, server):
    mk(client, "Im Backup", "2033-01-03T10:00")
    first = client.post(f"{API}/backups").json()
    name = first["name"]
    data = client.get(f"{API}/backups/{name}").content
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db"); os.write(fd, data); os.close(fd)
    con = sqlite3.connect(path)
    assert con.execute("SELECT count(*) FROM events WHERE title = 'Im Backup'").fetchone()[0] == 1
    con.close(); os.unlink(path)
    for _ in range(3):
        time.sleep(1.1)
        client.post(f"{API}/backups")
    assert len(client.get(f"{API}/backups").json()["backups"]) >= 4
    assert client.get(f"{API}/backups/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)
    assert client.get(f"{API}/backups/nicht-da.db").status_code in (400, 404)
    assert "VCALENDAR" in client.get(f"{API}/backups/{name.replace('.db', '.ics')}").text


def test_settings_roundtrip_and_validation(client):
    s = client.get(f"{API}/settings").json()
    assert s["week_start"] == 1 and s["theme"] == "dark"
    assert client.put(f"{API}/settings", json={"theme": "light", "hide_weekends": True}).json()["theme"] == "light"
    assert client.get(f"{API}/settings").json()["hide_weekends"] is True
    assert client.put(f"{API}/settings", json={"gibtsnicht": 1}).status_code == 400
    assert client.put(f"{API}/settings", json={"theme": 5}).status_code == 400
    client.put(f"{API}/settings", json={"theme": "dark", "hide_weekends": False})


def test_meta_health_and_palette_contrast(client):
    assert client.get(f"{API}/health").json()["ok"]
    m = client.get(f"{API}/meta").json()
    assert m["timezone"] == "Europe/Berlin" and "blau" in m["palette"] and m["version"]
    for key, c in palette.palette().items():
        assert palette.contrast(c["fill"], c["on"]) >= 4.5, key            # Text auf dem Termin-Block
        assert palette.contrast(c["fill"], "#000000") >= 7, key            # Punkte/Balken auf Schwarz


def test_live_stream_delivers_changes_and_alarms(server):
    got: list[str] = []
    ready = threading.Event()

    def reader():
        with httpx.stream("GET", f"{server}{API}/stream", timeout=15) as r:
            ready.set()
            for line in r.iter_lines():
                if line.startswith("data:"):
                    got.append(line[5:].strip())
                    if len(got) >= 2:
                        return

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    ready.wait(5); time.sleep(0.5)
    ida = httpx.Client(base_url=server, headers={"Authorization": f"Bearer {TOKEN}", "X-Kalender-By": "ida"})
    ida.post(f"{API}/events", json={"title": "Live", "start": "2033-02-01T10:00"})
    ida.post(f"{API}/events", json={"title": "Live 2", "start": "2033-02-01T11:00"})
    th.join(10)
    assert len(got) >= 2 and '"type": "changed"' in got[0] and '"by": "ida"' in got[0] and '"title": "Live"' in got[0]


def test_nothing_goes_out_to_the_internet(client):
    # die Netz-Sperre (conftest) würde jede Verbindung nach außen als Fehler melden; hier nur: alle Seiten laufen offline
    for path in ("/api/v1/meta", "/api/v1/calendars", "/api/v1/settings"):
        assert client.get(path).status_code == 200
