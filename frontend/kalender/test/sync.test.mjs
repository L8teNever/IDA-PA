import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";
import * as db from "../js/db.js";
import * as repo from "../js/repo.js";
import { desiredWindow, plan } from "../js/sync.js";

beforeEach(() => db.setBackend(db.memBackend()));
const o = (id, start, end, extra = {}) => ({ id, event_id: id.split("_")[0], calendar_id: "c1", title: id, start, end, all_day: false, ...extra });
const res = (extra = {}) => ({ cursor: 10, full: false, from: "2026-07-01", to: "2027-11-01", upserts: [], removed_events: [], calendars: null, settings: null, meta_at: "0", ...extra });

describe("Zeitraum und Plan", () => {
  test("−3 bis +12 Monate in vollen Monaten", () => {
    assert.deepEqual(desiredWindow(new Date(2026, 9, 30)), { from: "2026-07-01", to: "2027-10-01" });
    assert.deepEqual(desiredWindow(new Date(2026, 0, 15)), { from: "2025-10-01", to: "2027-01-01" });
  });
  test("erster Abgleich und neuer Tag sind voll, sonst Delta mit gemerktem Zeitraum", () => {
    const now = new Date(2026, 9, 30, 12);
    assert.equal(plan(undefined, { now }).full, true);
    assert.equal(plan({ cursor: 5, fullDay: "2026-10-29", from: "a", to: "b" }, { now }).full, true);
    const p = plan({ cursor: 5, fullDay: "2026-10-30", from: "2026-07-01", to: "2027-10-01", metaAt: "x" }, { now });
    assert.deepEqual([p.full, p.since, p.from, p.to, p.metaAt], [false, 5, "2026-07-01", "2027-10-01", "x"]);
    assert.equal(plan({ cursor: 5, fullDay: "2026-10-30", from: "a", to: "b" }, { now, full: true }).full, true);
  });
});

describe("Abgleich anwenden", () => {
  test("voller Stand ersetzt alles, merkt Cursor, Zeitraum und Tag", async () => {
    await db.put("occ", db.normOcc(o("alt_1", "2026-11-05T10:00:00+01:00", "2026-11-05T11:00:00+01:00")));
    await db.put("events", { event_id: "alt", _for: "alt_1", title: "alt" });
    const r = await repo.apply(res({ full: true, cursor: 7, upserts: [o("neu_1", "2026-11-06T10:00:00+01:00", "2026-11-06T11:00:00+01:00")], calendars: [{ id: "c1" }] }));
    assert.deepEqual([r.full, r.upserts], [true, 1]);
    assert.deepEqual((await db.all("occ")).map((x) => x.id), ["neu_1"]);
    assert.equal(await db.count("events"), 0, "alte Details sind weg");
    const m = await repo.syncMeta();
    assert.deepEqual([m.cursor, m.from, m.to, m.metaAt], [7, "2026-07-01", "2027-11-01", "0"]);
    assert.ok(m.fullDay);
    assert.deepEqual(await repo.loadMeta("cals"), [{ id: "c1" }]);
    assert.equal((await repo.windowInfo()).from, "2026-07-01");
  });

  test("Delta ersetzt ALLE Vorkommen der genannten Termine, lässt andere stehen, entfernt Gelöschte", async () => {
    await repo.apply(res({ full: true, upserts: [
      o("s_a", "2026-11-02T08:00:00+01:00", "2026-11-02T09:00:00+01:00"), o("s_b", "2026-11-09T08:00:00+01:00", "2026-11-09T09:00:00+01:00"),
      o("x_1", "2026-11-03T08:00:00+01:00", "2026-11-03T09:00:00+01:00"), o("g_1", "2026-11-04T08:00:00+01:00", "2026-11-04T09:00:00+01:00")] }));
    await db.put("events", { event_id: "s", _for: "s_a", title: "Detail" });
    const r = await repo.apply(res({ cursor: 11, upserts: [o("s_c", "2026-11-16T08:00:00+01:00", "2026-11-16T09:00:00+01:00")], removed_events: ["g"] }));
    assert.deepEqual([r.full, r.upserts, r.removed], [false, 1, 1]);
    assert.deepEqual((await db.all("occ")).map((x) => x.id).sort(), ["s_c", "x_1"]);   // s_a/s_b ersetzt, g weg, x unberührt
    assert.equal(await db.get("events", "s"), undefined, "Details des geänderten Termins werden neu geholt");
    const m = await repo.syncMeta();
    assert.deepEqual([m.cursor, m.from], [11, "2026-07-01"], "Zeitraum bleibt der des vollen Abgleichs");
  });

  test("Termine mit wartender Änderung (protectedIds) bleiben unberührt", async () => {
    await repo.apply(res({ full: true, upserts: [o("p_1", "2026-11-02T08:00:00+01:00", "2026-11-02T09:00:00+01:00", { title: "meine Vorschau" })] }));
    await repo.apply(res({ cursor: 12, upserts: [o("p_1", "2026-11-02T08:00:00+01:00", "2026-11-02T09:00:00+01:00", { title: "vom Server" })], removed_events: [] }), { protectedIds: new Set(["p"]) });
    assert.equal((await db.get("occ", "p_1")).title, "meine Vorschau");
    await repo.apply(res({ full: true, upserts: [] }), { protectedIds: new Set(["p"]) });
    assert.equal(await db.count("occ"), 1, "auch ein voller Abgleich fasst sie nicht an");
  });

  test("Details: gespeichert → sofort; für ein anderes Vorkommen nicht; offline zumindest die Liste", async () => {
    await repo.apply(res({ full: true, upserts: [o("e_1", "2026-11-02T08:00:00+01:00", "2026-11-02T09:00:00+01:00", { location: "R12" })] }));
    assert.equal(await repo.cachedDetail("e_1"), null);
    await repo.putDetail("e_1", { id: "e_1", event_id: "e", title: "Voll", description: "Notiz" });
    assert.equal((await repo.cachedDetail("e_1")).description, "Notiz");
    assert.equal(await repo.cachedDetail("e_2"), null);
    const part = await repo.occDetail("e_1");
    assert.deepEqual([part.partial, part.location, part.day], [true, "R12", undefined]);
    assert.equal(await repo.occDetail("gibtsnicht"), null);
  });

  test("Hörer werden benachrichtigt; ein kaputter stört die anderen nicht", () => {
    const seen = [];
    repo.onChange(() => { throw new Error("kaputt"); });
    const off = repo.onChange((w) => seen.push(w));
    repo.emit("sync"); off(); repo.emit("sync");
    assert.deepEqual(seen, ["sync"]);
  });
});
