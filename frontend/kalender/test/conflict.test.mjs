import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { autoMerge, diff, norm, rebase, recreateBody } from "../js/conflict.js";

const entry = (body, base, server, extra = {}) => ({ type: "update", target: "abc123", scope: "all", state: "conflict", baseSequence: 1, body, base, server: { sequence: 2, all_day: false, ...server }, ...extra });

describe("Vergleichen", () => {
  test("Zeiten nur auf die Minute, Listen ohne Reihenfolge, leer ist leer", () => {
    assert.equal(norm("start", "2026-11-05T15:00:00+01:00"), norm("start", "2026-11-05T15:00"));
    assert.equal(norm("alarms", [60, 10]), norm("alarms", [10, 60]));
    assert.equal(norm("location", null), norm("location", ""));
    assert.notEqual(norm("title", "A"), norm("title", "a"));
  });
});

describe("diff / autoMerge", () => {
  test("verschiedene Felder → zusammenführen (ich: Titel, Server: Ort)", () => {
    const e = entry({ title: "Neu" }, { title: "Alt" }, { title: "Alt", location: "Raum 5" });
    assert.deepEqual(diff(e).map((r) => [r.key, r.serverChanged, r.same]), [["title", false, false]]);
    assert.equal(autoMerge(e).ok, true);
  });
  test("dasselbe Feld, andere Werte → Konflikt mit beiden Werten", () => {
    const e = entry({ start: "2026-11-05T16:00" }, { start: "2026-11-05T15:00:00+01:00" }, { start: "2026-11-05T17:00:00+01:00" });
    const m = autoMerge(e);
    assert.equal(m.ok, false);
    assert.deepEqual([m.conflicts[0].key, m.conflicts[0].mine, m.conflicts[0].server], ["start", "2026-11-05T16:00", "2026-11-05T17:00:00+01:00"]);
  });
  test("dasselbe Feld, aber derselbe Wert → kein Konflikt", () => {
    const e = entry({ title: "Gleich" }, { title: "Alt" }, { title: "Gleich" });
    assert.equal(autoMerge(e).ok, true);
  });
  test("Titel gleichzeitig geändert, Zeit nur von mir → nur der Titel ist Konflikt", () => {
    const e = entry({ title: "Meiner", start: "2026-11-05T16:00" }, { title: "Alt", start: "2026-11-05T15:00:00+01:00" }, { title: "Ihrer", start: "2026-11-05T15:00:00+01:00" });
    assert.deepEqual(autoMerge(e).conflicts.map((r) => r.key), ["title"]);
  });
  test("ohne gemerkten Ausgangswert wird vorsichtig als Konflikt gewertet, sobald sich die Werte unterscheiden", () => {
    const e = entry({ title: "Meiner" }, {}, { title: "Ihrer" });
    assert.equal(autoMerge(e).ok, false);
  });
  test("Serien-Vorkommen, gelöschte Termine und Löschungen werden nie automatisch zusammengeführt", () => {
    assert.equal(autoMerge(entry({ title: "x" }, { title: "a" }, { title: "a" }, { target: "abc123_20261105T1000" })).ok, false);
    assert.equal(autoMerge(entry({ title: "x" }, { title: "a" }, { title: "a" }, { gone: true })).ok, false);
    assert.equal(autoMerge(entry({}, {}, { title: "a" }, { type: "delete" })).ok, false);
  });
  test("Erinnerungen und Wiederholung werden verglichen", () => {
    assert.equal(autoMerge(entry({ alarms: [10] }, { alarms: [30] }, { alarms: [30] })).ok, true);
    assert.equal(autoMerge(entry({ alarms: [10] }, { alarms: [30] }, { alarms: [60] })).ok, false);
    assert.equal(diff(entry({ repeat: "weekly" }, { rrule: "" }, { rrule: "FREQ=DAILY" }))[0].serverChanged, true);
  });
});

describe("Neu aufsetzen", () => {
  test("rebase nimmt die Sequenz des Servers, setzt den Zustand zurück und zählt automatische Zusammenführungen", () => {
    const e = entry({ title: "x" }, {}, { sequence: 7 });
    assert.deepEqual({ ...rebase(e), opId: "x" }, { opId: "x", state: "pending", tries: 0, nextTry: 0, error: "", gone: false, server: null, baseSequence: 7, merges: 0 });
    assert.equal(rebase({ ...e, merges: 1 }, true).merges, 2);
  });
  test("recreateBody: letzter Stand + meine Änderungen, neue uid, Zeiten ohne Offset", () => {
    const b = recreateBody({ title: "Alt", location: "Ort", start: "2026-11-05T15:00:00+01:00", end: "2026-11-05T16:00:00+01:00", all_day: false, sequence: 4, id: "x" }, { title: "Meiner" });
    assert.deepEqual([b.title, b.location, b.start, b.end], ["Meiner", "Ort", "2026-11-05T15:00", "2026-11-05T16:00"]);
    assert.match(b.uid, /@ida-kalender$/);
    assert.equal(b.sequence, undefined); assert.equal(b.id, undefined);
  });
});
