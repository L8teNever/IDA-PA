import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import * as db from "../js/db.js";
import * as repo from "../js/repo.js";

beforeEach(() => db.setBackend(db.memBackend()));
const o = (id, start, end, extra = {}) => ({ id, event_id: id.split("_")[0], title: id, start, end, all_day: false, ...extra });

test("range liefert Vorkommen, die den Bereich berühren – auch mehrtägige von davor", async () => {
  await repo.saveRange([
    o("a_1", "2026-11-05T15:00:00+01:00", "2026-11-05T16:00:00+01:00"),
    o("u_1", "2026-11-03", "2026-11-07", { all_day: true }),
    o("n_1", "2026-11-04T22:00:00+01:00", "2026-11-05T06:00:00+01:00"),
    o("x_1", "2026-11-20T10:00:00+01:00", "2026-11-20T11:00:00+01:00"),
  ], "2026-11-01", "2026-12-01");
  const ids = async (a, b) => (await repo.range(a, b)).map((e) => e.id);
  assert.deepEqual(await ids("2026-11-05", "2026-11-06"), ["u_1", "n_1", "a_1"]);   // ganztägig zuerst, dann nach Zeit
  assert.deepEqual(await ids("2026-11-06", "2026-11-07"), ["u_1"]);
  assert.deepEqual(await ids("2026-11-07", "2026-11-08"), []);                     // Ende (exklusiv) berührt nicht
  assert.deepEqual(await ids(new Date(2026, 10, 20), new Date(2026, 10, 21)), ["x_1"]);
  assert.equal((await repo.range("2026-11-05", "2026-11-06"))[0].day, undefined, "interne Felder bleiben draußen");
});

test("saveRange ersetzt den Bereich, lässt anderes in Ruhe und erweitert das Fenster", async () => {
  await repo.saveRange([o("a_1", "2026-11-05T10:00:00+01:00", "2026-11-05T11:00:00+01:00"), o("b_1", "2026-11-25T10:00:00+01:00", "2026-11-25T11:00:00+01:00")], "2026-11-01", "2026-12-01");
  await repo.saveRange([o("c_1", "2026-11-06T10:00:00+01:00", "2026-11-06T11:00:00+01:00")], "2026-11-01", "2026-11-10");
  assert.deepEqual((await repo.range("2026-11-01", "2026-12-01")).map((e) => e.id), ["c_1", "b_1"]);   // a_1 war im ersetzten Bereich
  await repo.saveRange([], "2026-12-01", "2027-01-01");
  const w = await repo.windowInfo();
  assert.deepEqual([w.from, w.to], ["2026-11-01", "2027-01-01"]);
});

test("everything liefert alles für die Offline-Suche", async () => {
  await repo.saveRange([o("a_1", "2026-11-05T10:00:00+01:00", "2026-11-05T11:00:00+01:00")], "2026-11-01", "2026-12-01");
  const { events, window } = await repo.everything();
  assert.equal(events.length, 1); assert.equal(window.from, "2026-11-01");
  assert.equal(events[0].last, undefined);
});

test("ohne gespiegeltes Fenster gibt es offline nichts zu zeigen", async () => {
  assert.equal(await repo.windowInfo(), undefined);
});
