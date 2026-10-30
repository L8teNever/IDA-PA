// Node-Tests für den lokalen Speicher (ohne Browser, ohne Pakete): node --test frontend/kalender/test
import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";
import * as db from "../js/db.js";

beforeEach(() => db.setBackend(db.memBackend()));

const occ = (id, start, end, extra = {}) => ({ id, event_id: id.split("_")[0], title: id, start, end, all_day: false, ...extra });

describe("Tagesfelder", () => {
  test("dayAdd über Monats- und Jahresgrenzen", () => {
    assert.equal(db.dayAdd("2026-10-31", 1), "2026-11-01");
    assert.equal(db.dayAdd("2026-12-31", 1), "2027-01-01");
    assert.equal(db.dayAdd("2028-03-01", -1), "2028-02-29");
    assert.equal(db.dayAdd("2026-03-29", 0), "2026-03-29");
  });
  test("lastDay: ganztägig (Ende exklusiv), über Mitternacht, Ende genau 00:00", () => {
    assert.equal(db.lastDay(occ("a", "2026-12-24", "2026-12-28", { all_day: true })), "2026-12-27");
    assert.equal(db.lastDay(occ("a", "2026-12-24", "2026-12-25", { all_day: true })), "2026-12-24");
    assert.equal(db.lastDay(occ("a", "2026-11-30T22:00:00+01:00", "2026-12-01T06:00:00+01:00")), "2026-12-01");
    assert.equal(db.lastDay(occ("a", "2026-11-30T22:00:00+01:00", "2026-12-01T00:00:00+01:00")), "2026-11-30");
    assert.equal(db.lastDay(occ("a", "2026-11-05T15:00:00+01:00", "2026-11-05T16:00:00+01:00")), "2026-11-05");
  });
});

describe("Speicher", () => {
  test("put/get/del/all mit Indizes, sortiert wie IndexedDB", async () => {
    await db.put("occ", db.normOcc(occ("b_1", "2026-11-06T10:00:00+01:00", "2026-11-06T11:00:00+01:00")));
    await db.put("occ", db.normOcc(occ("a_1", "2026-11-05T10:00:00+01:00", "2026-11-05T11:00:00+01:00")));
    await db.put("occ", db.normOcc(occ("c_1", "2026-12-01T10:00:00+01:00", "2026-12-01T11:00:00+01:00")));
    assert.equal((await db.get("occ", "a_1")).day, "2026-11-05");
    assert.deepEqual((await db.all("occ", { index: "day", gte: "2026-11-01", lte: "2026-11-30" })).map((x) => x.id), ["a_1", "b_1"]);
    assert.deepEqual((await db.all("occ", { index: "event_id", eq: "c" })).map((x) => x.id), ["c_1"]);
    await db.del("occ", "a_1");
    assert.equal(await db.get("occ", "a_1"), undefined);
    assert.equal(await db.count("occ"), 2);
  });

  test("Outbox: automatische Nummern, nur steigend, auch nach Löschen", async () => {
    const s1 = await db.put("outbox", { type: "create", target: "x", state: "pending" });
    const s2 = await db.put("outbox", { type: "update", target: "x", state: "pending" });
    assert.ok(s2 > s1);
    assert.equal((await db.get("outbox", s1)).seq, s1);
    await db.del("outbox", s2);
    const s3 = await db.put("outbox", { type: "delete", target: "y", state: "pending" });
    assert.ok(s3 > s2);
    assert.deepEqual((await db.all("outbox", { index: "target", eq: "x" })).map((x) => x.type), ["create"]);
  });

  test("batch ist alles oder nichts", async () => {
    await db.put("occ", db.normOcc(occ("a_1", "2026-11-05T10:00:00+01:00", "2026-11-05T11:00:00+01:00")));
    await assert.rejects(db.batch([
      { op: "del", store: "occ", key: "a_1" },
      { op: "put", store: "occ", value: db.normOcc(occ("z_1", "2026-11-07T10:00:00+01:00", "2026-11-07T11:00:00+01:00")) },
      { op: "put", store: "occ", value: undefined }, // wirft
    ]));
    assert.ok(await db.get("occ", "a_1"), "der gelöschte Eintrag ist wieder da");
    assert.equal(await db.get("occ", "z_1"), undefined, "der neue Eintrag wurde nicht geschrieben");
  });

  test("Werte werden kopiert (kein versehentliches Mitändern)", async () => {
    const v = { id: "k", event_id: "k", start: "2026-01-01", end: "2026-01-02", all_day: true, tags: [1] };
    await db.put("occ", v); v.tags.push(2);
    assert.deepEqual((await db.get("occ", "k")).tags, [1]);
  });
});

describe("Übernahme aus Schema 1", () => {
  const v1 = () => ({
    window: { at: 1700000000000, from: "2026-07-01", to: "2027-03-01", events: [
      occ("a_1", "2026-11-05T15:00:00+01:00", "2026-11-05T16:00:00+01:00"),
      occ("u_1", "2026-12-24", "2026-12-27", { all_day: true }),
      occ("n_1", "2026-11-30T22:00:00+01:00", "2026-12-01T06:00:00+01:00"),
    ] },
    cals: [{ id: "c1", name: "Privat" }], settings: { week_start: 1 },
  });

  test("alter Spiegel landet in occ und meta; zweimal ausführen ändert nichts", async () => {
    const be = db.memBackend();
    for (const [k, v] of Object.entries(v1())) await be.put("kv", v, k);
    const r = await db.migrate(be);
    assert.deepEqual([r.migrated, r.occ], [true, 3]);
    const rows = await be.all("occ");
    assert.deepEqual(rows.map((x) => [x.id, x.day, x.last]).sort(), [["a_1", "2026-11-05", "2026-11-05"], ["n_1", "2026-11-30", "2026-12-01"], ["u_1", "2026-12-24", "2026-12-26"]]);
    assert.deepEqual(await be.get("meta", "window"), { at: 1700000000000, from: "2026-07-01", to: "2027-03-01" });
    assert.deepEqual(await be.get("meta", "cals"), [{ id: "c1", name: "Privat" }]);
    assert.equal(await be.get("meta", "schema"), db.SCHEMA);
    assert.deepEqual(await db.migrate(be), { skipped: true });
    assert.equal((await be.all("occ")).length, 3);
  });

  test("neue Installation ohne alte Daten: nur die Versionsmarke", async () => {
    const be = db.memBackend();
    assert.deepEqual((await db.migrate(be)).occ, 0);
    assert.equal(await be.get("meta", "schema"), db.SCHEMA);
    assert.equal(await be.get("meta", "window"), undefined);
  });

  test("kaputte Einträge im alten Spiegel werden übergangen, der Rest bleibt", async () => {
    const be = db.memBackend();
    await be.put("kv", { at: 1, from: "a", to: "b", events: [null, { title: "ohne Zeit" }, occ("ok_1", "2026-11-05T10:00:00+01:00", "2026-11-05T11:00:00+01:00")] }, "window");
    assert.equal((await db.migrate(be)).occ, 1);
  });

  test("Outbox-Einträge überstehen die Übernahme (outbox wird nie angefasst)", async () => {
    const be = db.memBackend();
    await be.put("outbox", { type: "update", target: "a_1", state: "pending" });
    await be.put("kv", v1().window, "window");
    await db.migrate(be);
    assert.equal((await be.all("outbox")).length, 1);
  });
});

describe("b(): Notnagel ohne IndexedDB", () => {
  test("ohne indexedDB läuft der Speicher im Arbeitsspeicher und meldet es", async () => {
    db.setBackend(null);
    assert.equal(globalThis.indexedDB, undefined);
    await db.put("meta", 5, "x");
    assert.equal(await db.get("meta", "x"), 5);
    assert.equal(db.state.ok, false);
    assert.equal(db.state.kind, "memory");
    assert.match(db.state.error, /IndexedDB/);
  });
});
