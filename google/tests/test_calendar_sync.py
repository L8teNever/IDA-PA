import itertools
import unittest
from datetime import datetime, timezone

from app.google_client import GoogleApiError
from app.jobs import calendar_sync as cs
from app.jobs.state import State

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class FakeKal:
    """Bildet die Kalender-API nach: Termine als Detail-Dicts."""

    def __init__(self):
        self.events, self._n, self.deleted = {}, itertools.count(1), []

    def calendars(self):
        return [{"id": "c1", "name": "Privat", "kind": "normal", "is_default": True}, {"id": "h", "name": "Feiertage", "kind": "holidays", "read_only": True}]

    def event_ids(self, calendar_id, frm, to):
        return sorted(self.events)

    def get(self, i):
        return self.events.get(i)

    def _make(self, i, body, updated="2026-11-10T10:00:00+01:00"):
        e = {"event_id": i, "id": i, "updated_at": updated, "tzid": body.get("tzid", "Europe/Berlin"), "rrule": body.get("rrule", ""), "parent_id": None,
             **{k: body.get(k, "") for k in ("title", "description", "location")}, "all_day": bool(body.get("all_day")),
             "start": body["start"], "end": body["end"]}
        self.events[i] = e
        return e

    def create(self, body):
        return self._make(f"k{next(self._n)}", body)

    def update(self, i, body):
        return self._make(i, {**self.events[i], **body}, "2026-11-10T11:00:00+01:00")

    def delete(self, i):
        self.events.pop(i, None)
        self.deleted.append(i)


class FakeGoogle:
    def __init__(self):
        self.events, self._n = {}, itertools.count(1)

    def list(self, tmin, tmax):
        return list(self.events.values())

    def get(self, i):
        return self.events.get(i)

    def _store(self, i, body, updated):
        old = self.events.get(i, {})
        e = {"id": i, "status": "confirmed", "updated": updated, **{k: v for k, v in old.items() if k not in body}}
        for k, v in body.items():
            if k in ("start", "end"):
                v = {a: b for a, b in v.items() if b is not None}
            e[k] = v
        self.events[i] = e
        return e

    def create(self, body):
        return self._store(f"g{next(self._n)}", body, "2026-11-10T10:00:00Z")

    def update(self, i, body):
        return self._store(i, body, "2026-11-10T11:00:00Z")

    def delete(self, i):
        self.events.pop(i, None)


def timed(title, start="2026-11-12T15:00:00+01:00", end="2026-11-12T16:00:00+01:00", **kw):
    return {"title": title, "start": start, "end": end, **kw}


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.k, self.g, self.st = FakeKal(), FakeGoogle(), State(":memory:")

    def sync(self, dry=False, max_deletes=20):
        return cs.CalendarSync(self.k, self.g, self.st, now=NOW, max_deletes=max_deletes).run(dry_run=dry)

    def test_neuer_termin_geht_nach_google_und_zurueck_nichts(self):
        self.k.create(timed("Zahnarzt", description="Kontrolle", location="Praxis"))
        r = self.sync()
        self.assertEqual((r["new_in_google"], r["errors"]), (1, []))
        g = next(iter(self.g.events.values()))
        self.assertEqual((g["summary"], g["location"], g["start"]["dateTime"]), ("Zahnarzt", "Praxis", "2026-11-12T15:00:00+01:00"))
        r2 = self.sync()   # nichts geaendert: keine Echo-Schleife
        self.assertEqual((r2["push"], r2["pull"], r2["new_in_google"], r2["new_in_ida"]), (0, 0, 0, 0))

    def test_neuer_google_termin_kommt_nach_ida(self):
        self.g.create({"summary": "Konzert", "start": {"dateTime": "2026-11-20T19:00:00+01:00", "timeZone": "Europe/Berlin"},
                       "end": {"dateTime": "2026-11-20T22:00:00+01:00", "timeZone": "Europe/Berlin"}})
        r = self.sync()
        self.assertEqual(r["new_in_ida"], 1)
        k = next(iter(self.k.events.values()))
        self.assertEqual((k["title"], k["start"]), ("Konzert", "2026-11-20T19:00:00+01:00"))
        self.assertEqual(self.sync()["new_in_ida"], 0)

    def test_gleicher_termin_auf_beiden_seiten_wird_verbunden_nicht_doppelt(self):
        self.k.create(timed("Sport"))
        self.g.create({"summary": "sport", "start": {"dateTime": "2026-11-12T14:00:00Z"}, "end": {"dateTime": "2026-11-12T15:00:00Z"}})
        r = self.sync()
        self.assertEqual((r["linked"], r["new_in_google"], r["new_in_ida"]), (1, 0, 0))
        self.assertEqual((len(self.k.events), len(self.g.events)), (1, 1))

    def test_aenderung_in_ida_geht_nach_google(self):
        k = self.k.create(timed("Meeting"))
        self.sync()
        self.k.update(k["event_id"], {"title": "Meeting verschoben", "start": "2026-11-13T15:00:00+01:00", "end": "2026-11-13T16:00:00+01:00"})
        r = self.sync()
        self.assertEqual(r["push"], 1)
        self.assertEqual(next(iter(self.g.events.values()))["summary"], "Meeting verschoben")
        self.assertEqual(self.sync()["push"], 0)

    def test_aenderung_in_google_geht_nach_ida(self):
        self.k.create(timed("Lernen"))
        self.sync()
        gid = next(iter(self.g.events))
        self.g.update(gid, {"summary": "Lernen fuer Mathe", "location": "Bibliothek"})
        r = self.sync()
        self.assertEqual(r["pull"], 1)
        k = next(iter(self.k.events.values()))
        self.assertEqual((k["title"], k["location"]), ("Lernen fuer Mathe", "Bibliothek"))

    def test_beide_geaendert_juengere_gewinnt(self):
        k = self.k.create(timed("A"))
        self.sync()
        gid = next(iter(self.g.events))
        self.k.events[k["event_id"]].update(title="Aus IDA", updated_at="2026-11-10T09:00:00+01:00")
        self.g.update(gid, {"summary": "Aus Google"})   # updated 11:00Z, spaeter
        self.sync()
        self.assertEqual(self.k.events[k["event_id"]]["title"], "Aus Google")

    def test_loeschen_in_ida_loescht_in_google_und_umgekehrt(self):
        a, b = self.k.create(timed("Weg1")), self.k.create(timed("Weg2", start="2026-11-14T10:00:00+01:00", end="2026-11-14T11:00:00+01:00"))
        self.sync()
        self.k.delete(a["event_id"])
        gid_b = [i for i, e in self.g.events.items() if e["summary"] == "Weg2"][0]
        self.g.delete(gid_b)
        r = self.sync()
        self.assertEqual((r["deleted_google"], r["deleted_ida"]), (1, 1))
        self.assertEqual((self.k.events, self.g.events), ({}, {}))

    def test_sicherheitsnetz_bei_vielen_loeschungen(self):
        for i in range(5):
            self.k.create(timed(f"T{i}", start=f"2026-11-{12 + i}T10:00:00+01:00", end=f"2026-11-{12 + i}T11:00:00+01:00"))
        self.sync()
        self.k.events.clear()
        with self.assertRaises(cs.SyncAbort):
            self.sync(max_deletes=3)
        self.assertEqual(len(self.g.events), 5)

    def test_probelauf_aendert_nichts(self):
        self.k.create(timed("Test"))
        r = self.sync(dry=True)
        self.assertEqual((r["new_in_google"], self.g.events, self.st.pairs()), (1, {}, []))
        self.assertIn("Neu in Google: Test", r["plan"])

    def test_ganztaegig_und_serie(self):
        self.k.create({"title": "Urlaub", "all_day": True, "start": "2026-12-01", "end": "2026-12-04"})
        self.k.create(timed("Training", rrule="FREQ=WEEKLY;BYDAY=TU;UNTIL=20261231T235959"))
        self.sync()
        by = {e["summary"]: e for e in self.g.events.values()}
        self.assertEqual((by["Urlaub"]["start"], by["Urlaub"]["end"]), ({"date": "2026-12-01"}, {"date": "2026-12-04"}))
        self.assertEqual(by["Training"]["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL=20261231T225959Z"])
        self.assertEqual(self.sync()["push"], 0)

    def test_einzelne_vorkommen_werden_uebersprungen(self):
        self.k.create(timed("Ausnahme"))
        next(iter(self.k.events.values()))["parent_id"] = "irgendwas"
        self.g.create({"summary": "Ausn", "recurringEventId": "x", "start": {"date": "2026-11-12"}, "end": {"date": "2026-11-13"}})
        r = self.sync()
        self.assertEqual((len(self.g.events), len(self.k.events), len(r["skipped"])), (1, 1, 2))
        self.assertEqual(self.st.pairs(), [])


class HelperTest(unittest.TestCase):
    def test_until_formate(self):
        self.assertEqual(cs._until_for_google("FREQ=DAILY;UNTIL=20261231T235959", "Europe/Berlin", False), "FREQ=DAILY;UNTIL=20261231T225959Z")
        self.assertEqual(cs._until_for_google("FREQ=DAILY;UNTIL=20261231T235959", "Europe/Berlin", True), "FREQ=DAILY;UNTIL=20261231")

    def test_fehler_wird_gemeldet_statt_abzubrechen(self):
        k, g, st = FakeKal(), FakeGoogle(), State(":memory:")
        k.create(timed("A"))
        g.create = lambda body: (_ for _ in ()).throw(GoogleApiError("kaputt"))
        r = cs.CalendarSync(k, g, st, now=NOW).run()
        self.assertEqual(len(r["errors"]), 1)


if __name__ == "__main__":
    unittest.main()
