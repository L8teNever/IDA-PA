import assert from "node:assert/strict";
import { describe, test } from "node:test";
import * as A from "../js/alarms.js";

const ev = (o = {}) => ({ id: "e1_20261103T0900", event_id: "e1", title: "Zahnarzt", start: "2026-11-03T09:00:00+01:00", end: "2026-11-03T10:00:00+01:00", all_day: false, alarms: [10], ...o });
const at = (s) => new Date(s);

describe("lokale Erinnerungen", () => {
  test("fällig genau zur Zeit und bis 15 Minuten danach", () => {
    assert.equal(A.due([ev()], at("2026-11-03T08:49:59+01:00")).length, 0);
    assert.equal(A.due([ev()], at("2026-11-03T08:50:00+01:00")).length, 1);
    assert.equal(A.due([ev()], at("2026-11-03T09:04:00+01:00")).length, 1);
    assert.equal(A.due([ev()], at("2026-11-03T09:06:00+01:00")).length, 0, "länger als 15 Minuten her: nicht mehr nachholen");
  });
  test("schon gezeigt = nicht noch einmal", () => {
    const d = A.due([ev()], at("2026-11-03T08:55:00+01:00"));
    const shown = A.remember({}, d.map((x) => x.key), at("2026-11-03T08:55:00+01:00"));
    assert.equal(A.due([ev()], at("2026-11-03T08:56:00+01:00"), shown).length, 0);
  });
  test("mehrere Erinnerungen je Termin", () => {
    const l = A.due([ev({ alarms: [60, 10] })], at("2026-11-03T08:00:30+01:00"));
    assert.deepEqual(l.map((x) => x.minutes), [60]);
  });
  test("vorbei, abgesagt, gelöscht, Feiertage und Geburtstage ohne Erinnerung", () => {
    const now = at("2026-11-03T08:55:00+01:00");
    assert.equal(A.due([ev({ status: "cancelled" })], now).length, 0);
    assert.equal(A.due([ev({ _pending: "delete" })], now).length, 0);
    assert.equal(A.due([ev({ holiday: true })], now).length, 0);
    assert.equal(A.due([ev({ alarms: [] })], now).length, 0);
    assert.equal(A.due([ev({ end: "2026-11-03T08:00:00+01:00" })], now).length, 0);
  });
  test("ganztägig: Erinnerung relativ zu 00:00", () => {
    const a = ev({ all_day: true, start: "2026-11-03", end: "2026-11-04", alarms: [60] });
    assert.equal(A.due([a], at("2026-11-02T23:05:00")).length, 1);
    assert.equal(A.due([a], at("2026-11-02T22:50:00")).length, 0);
  });
  test("remember vergisst alte Einträge", () => {
    const now = at("2026-11-10T12:00:00+01:00");
    const r = A.remember({ alt: +now - A.KEEP - 1, neu: +now - 1000 }, ["x"], now);
    assert.deepEqual(Object.keys(r).sort(), ["neu", "x"]);
  });
});
