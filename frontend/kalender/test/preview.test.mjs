import assert from "node:assert/strict";
import { describe, test } from "node:test";
import * as L from "../js/localtime.js";
import { apply, ruleOf, starts } from "../js/preview.js";

const tz = "Europe/Berlin";
const cals = [{ id: "c1", name: "Privat", color: "blau", is_default: true }, { id: "c2", name: "Ida", color: "lila" }];
const ctx = (a = "2026-10-01", b = "2026-12-31") => ({ a, b, cals, tz });
const row = (id, start, end, extra = {}) => ({ id, event_id: id.split("_")[0], calendar_id: "c1", calendar: "Privat", title: id, start, end, all_day: false, color: "blau", sequence: 2, location: "", ...extra });
const op = (type, target, body = {}, extra = {}) => ({ type, target, body, scope: "all", state: "pending", ...extra });
const ids = (l) => l.map((o) => o.id);
const starts_ = (l) => l.map((o) => o.start.slice(0, 16));

describe("Ortszeit", () => {
  test("Versatz mit Sommer-/Winterzeit, auch am Umstellungstag", () => {
    assert.equal(L.iso(L.parse("2026-07-01T10:00"), tz), "2026-07-01T10:00:00+02:00");
    assert.equal(L.iso(L.parse("2026-12-01T10:00"), tz), "2026-12-01T10:00:00+01:00");
    assert.equal(L.iso(L.parse("2026-10-25T01:30"), tz), "2026-10-25T01:30:00+02:00");   // noch Sommerzeit
    assert.equal(L.iso(L.parse("2026-10-25T04:00"), tz), "2026-10-25T04:00:00+01:00");   // schon Winterzeit
    assert.equal(L.iso(L.parse("2027-03-28T03:30"), tz), "2027-03-28T03:30:00+02:00");
    assert.equal(L.iso(L.parse("2026-12-01T10:00"), "America/New_York"), "2026-12-01T10:00:00-05:00");
  });
  test("Rechnen: Tage/Minuten über Monats- und Jahresgrenzen, Wochentag", () => {
    assert.equal(L.dayStr(L.addDays(L.parse("2026-12-31"), 1)), "2027-01-01");
    assert.equal(L.dayStr(L.addMin(L.parse("2026-12-31T23:30"), 45)), "2027-01-01");
    assert.equal(L.weekday(L.parse("2026-10-30")), 4);     // Freitag
    assert.equal(L.key(L.parse("2026-11-05T09:05")), "20261105T0905");
  });
});

describe("Wiederholungen", () => {
  const s = (rule, start, a = "2026-11-01", b = "2026-12-01") => starts(rule, L.parse(start), a, b).map((t) => L.dayStr(t));
  test("ruleOf versteht Text, Objekt und RRULE; Unbekanntes ist „?“", () => {
    assert.deepEqual(ruleOf({ repeat: "daily" }), { freq: "daily", interval: 1, days: [], count: 0, until: "", nth: 0 });
    assert.deepEqual(ruleOf({ repeat: "weekdays" }).days, [0, 1, 2, 3, 4]);
    assert.deepEqual(ruleOf({ repeat: { freq: "weekly", days: ["mo", "mi"], until: "2026-12-20" } }).days, [0, 2]);
    assert.equal(ruleOf({ rrule: "FREQ=WEEKLY;BYDAY=FR;COUNT=3" }).count, 3);
    assert.equal(ruleOf({ rrule: "FREQ=MONTHLY;BYSETPOS=1;BYDAY=MO" }), "?");
    assert.equal(ruleOf({ repeat: "none" }), null);
    assert.equal(ruleOf({ repeat: "stündlich" }), "?");
    assert.equal(ruleOf({}), null);
  });
  test("täglich, werktags, wöchentlich, 14-tägig", () => {
    assert.deepEqual(s({ freq: "daily", interval: 10, days: [] }, "2026-11-02T08:00"), ["2026-11-02", "2026-11-12", "2026-11-22"]);
    assert.deepEqual(s({ freq: "weekly", interval: 1, days: [0, 1, 2, 3, 4] }, "2026-11-05T08:00", "2026-11-05", "2026-11-10"), ["2026-11-05", "2026-11-06", "2026-11-09"]);
    assert.deepEqual(s({ freq: "weekly", interval: 1, days: [] }, "2026-11-04T08:00"), ["2026-11-04", "2026-11-11", "2026-11-18", "2026-11-25"]);
    assert.deepEqual(s({ freq: "weekly", interval: 2, days: [] }, "2026-11-04T08:00"), ["2026-11-04", "2026-11-18"]);
  });
  test("Anzahl zählt von Anfang an, auch wenn der Bereich später beginnt; Ende zählt mit", () => {
    assert.deepEqual(s({ freq: "daily", interval: 1, days: [], count: 5 }, "2026-10-29T08:00", "2026-11-01", "2026-12-01"), ["2026-11-01", "2026-11-02"]);
    assert.deepEqual(s({ freq: "daily", interval: 1, days: [], until: "2026-11-03" }, "2026-11-01T08:00"), ["2026-11-01", "2026-11-02", "2026-11-03"]);
  });
  test("monatlich: am 31. nur in langen Monaten; der 2. Dienstag; der letzte Freitag", () => {
    assert.deepEqual(s({ freq: "monthly", interval: 1, days: [] }, "2026-01-31T10:00", "2026-01-01", "2026-06-01"), ["2026-01-31", "2026-03-31", "2026-05-31"]);
    assert.deepEqual(s({ freq: "monthly", interval: 1, days: [1], nth: 2 }, "2026-11-10T10:00", "2026-11-01", "2027-02-01"), ["2026-11-10", "2026-12-08", "2027-01-12"]);
    assert.deepEqual(s({ freq: "monthly", interval: 1, days: [4], nth: -1 }, "2026-11-27T10:00", "2026-11-01", "2027-02-01"), ["2026-11-27", "2026-12-25", "2027-01-29"]);
  });
  test("jährlich, auch der 29. Februar", () => {
    assert.deepEqual(s({ freq: "yearly", interval: 1, days: [] }, "2026-11-05T10:00", "2026-01-01", "2029-01-01"), ["2026-11-05", "2027-11-05", "2028-11-05"]);
    assert.deepEqual(s({ freq: "yearly", interval: 1, days: [] }, "2024-02-29T10:00", "2024-01-01", "2029-01-01"), ["2024-02-29", "2028-02-29"]);
  });
});

describe("Anlegen", () => {
  test("Einzeltermin: Ortszeit mit Versatz, Farbe aus dem Kalender, Vorschau-Markierung, nur im Bereich", () => {
    const out = apply([], [op("create", "local-aaa", { title: "Neu", start: "2026-11-05T15:00", end: "2026-11-05T16:30", calendar_id: "c2", location: "Raum 1" })], ctx());
    assert.equal(out.length, 1);
    assert.deepEqual([out[0].id, out[0].start, out[0].end, out[0].color, out[0].calendar, out[0]._pending, out[0].location],
      ["local-aaa", "2026-11-05T15:00:00+01:00", "2026-11-05T16:30:00+01:00", "lila", "Ida", "create", "Raum 1"]);
    assert.deepEqual(apply([], [op("create", "local-aaa", { title: "Neu", start: "2027-05-05T15:00" })], ctx()), []);
  });
  test("ohne Ende: duration_min oder eine Stunde; ganztägig mit exklusivem Ende", () => {
    assert.equal(apply([], [op("create", "local-a", { title: "x", start: "2026-11-05T15:00", duration_min: 30 })], ctx())[0].end, "2026-11-05T15:30:00+01:00");
    assert.equal(apply([], [op("create", "local-a", { title: "x", start: "2026-11-05T15:00" })], ctx())[0].end, "2026-11-05T16:00:00+01:00");
    const d = apply([], [op("create", "local-a", { title: "x", start: "2026-12-24", end: "2026-12-27", all_day: true })], ctx())[0];
    assert.deepEqual([d.all_day, d.start, d.end], [true, "2026-12-24", "2026-12-27"]);
  });
  test("Serie: ein Vorkommen je Termin mit eigener ID, nur im Bereich", () => {
    const out = apply([], [op("create", "local-s", { title: "Mathe", start: "2026-11-02T08:00", end: "2026-11-02T09:30", repeat: { freq: "weekly", days: [0], count: 3 } })], ctx());
    assert.deepEqual(ids(out), ["local-s_20261102T0800", "local-s_20261109T0800", "local-s_20261116T0800"]);
    assert.ok(out.every((o) => o.recurring && o._pending === "create"));
  });
  test("zu komplizierte Serie: nur der erste Termin, markiert als unvollständig", () => {
    const out = apply([], [op("create", "local-s", { title: "x", start: "2026-11-02T08:00", rrule: "FREQ=MONTHLY;BYSETPOS=1;BYDAY=MO" })], ctx());
    assert.equal(out.length, 1); assert.equal(out[0]._partial, true);
  });
  test("mehrtägiger Termin, der vor dem Bereich beginnt, zählt mit", () => {
    const out = apply([], [op("create", "local-n", { title: "Nacht", start: "2026-11-30T22:00", end: "2026-12-01T06:00" })], ctx("2026-12-01", "2026-12-02"));
    assert.equal(out.length, 1);
  });
});

describe("Ändern", () => {
  const base = () => [row("a1", "2026-11-05T15:00:00+01:00", "2026-11-05T16:00:00+01:00"), row("b2", "2026-11-06T09:00:00+01:00", "2026-11-06T10:00:00+01:00")];
  test("Felder ändern, nur den gemeinten Termin; die Dauer bleibt beim Verschieben", () => {
    const out = apply(base(), [op("update", "a1", { title: "Neu", location: "Ort", start: "2026-11-07T10:00" })], ctx());
    const a = out.find((o) => o.event_id === "a1");
    assert.deepEqual([a.title, a.location, a.start, a.end, a._pending], ["Neu", "Ort", "2026-11-07T10:00:00+01:00", "2026-11-07T11:00:00+01:00", "pending"]);
    assert.equal(out.find((o) => o.event_id === "b2").title, "b2");
  });
  test("Ende angegeben: neue Dauer; Kalender gewechselt: Name und Farbe folgen", () => {
    const out = apply(base(), [op("update", "a1", { start: "2026-11-05T15:00", end: "2026-11-05T18:00", calendar_id: "c2" })], ctx());
    assert.deepEqual([out[0].end, out[0].calendar, out[0].color], ["2026-11-05T18:00:00+01:00", "Ida", "lila"]);
  });
  test("Farbe des Termins gesetzt bleibt beim Kalenderwechsel", () => {
    const out = apply(base(), [op("update", "a1", { color: "rot", calendar_id: "c2" })], ctx());
    assert.equal(out[0].color, "rot");
  });
  const series = () => ["20261102T0800", "20261109T0800", "20261116T0800"].map((k) => row(`s1_${k}`, `${k.slice(0, 4)}-${k.slice(4, 6)}-${k.slice(6, 8)}T08:00:00+01:00`, `${k.slice(0, 4)}-${k.slice(4, 6)}-${k.slice(6, 8)}T09:00:00+01:00`, { recurring: true }));
  test("Serie: „nur dieser“, „dieser und folgende“, „alle“", () => {
    const t = (scope, target = "s1_20261109T0800") => apply(series(), [op("update", target, { title: "X", start: "2026-11-09T10:00" }, { scope })], ctx());
    assert.deepEqual(t("this").map((o) => `${o.title === "X" ? "X" : "-"}${o.start.slice(11, 16)}`), ["-08:00", "X10:00", "-08:00"]);
    assert.deepEqual(t("following").map((o) => `${o.title === "X" ? "X" : "-"}${o.start.slice(11, 16)}`), ["-08:00", "X10:00", "X10:00"]);
    assert.deepEqual(t("all").map((o) => `${o.title === "X" ? "X" : "-"}${o.start.slice(11, 16)}`), ["X10:00", "X10:00", "X10:00"]);
  });
  test("Serie verschieben: ganze Serie rückt um den Abstand des gezogenen Vorkommens", () => {
    const out = apply(series(), [op("update", "s1_20261109T0800", { start: "2026-11-10T08:00" })], ctx());
    assert.deepEqual(out.map((o) => o.start.slice(0, 10)), ["2026-11-03", "2026-11-10", "2026-11-17"]);
  });
  test("ganztägig ↔ mit Uhrzeit", () => {
    const out = apply(base(), [op("update", "a1", { all_day: true, start: "2026-11-05", end: "2026-11-06" })], ctx());
    assert.deepEqual([out[0].all_day, out[0].start, out[0].end], [true, "2026-11-05", "2026-11-06"]);
  });
  test("Wiederholung geändert: Vorkommen werden als unvollständig markiert", () => {
    const out = apply(series(), [op("update", "s1", { repeat: "daily" })], ctx());
    assert.ok(out.every((o) => o._partial));
  });
  test("Konflikt-/Fehler-Einträge bleiben sichtbar und sind entsprechend markiert", () => {
    assert.equal(apply(base(), [op("update", "a1", { title: "x" }, { state: "conflict" })], ctx())[0]._pending, "conflict");
    assert.equal(apply(base(), [op("update", "a1", { title: "x" }, { state: "error" })], ctx())[0]._pending, "error");
  });
  test("mehrere Änderungen werden der Reihe nach angewendet; ein unbekanntes Ziel stört nicht", () => {
    const out = apply(base(), [op("update", "a1", { title: "1" }), op("update", "a1", { title: "2" }), op("update", "zzz", { title: "?" })], ctx());
    assert.equal(out[0].title, "2");
  });
});

describe("Löschen und Kombinationen", () => {
  test("löschen blendet aus: ein Termin, ein Vorkommen, ab einem Vorkommen, die ganze Serie", () => {
    const s = ["20261102T0800", "20261109T0800", "20261116T0800"].map((k) => row(`s1_${k}`, `${k.slice(0, 4)}-${k.slice(4, 6)}-${k.slice(6, 8)}T08:00:00+01:00`, `${k.slice(0, 4)}-${k.slice(4, 6)}-${k.slice(6, 8)}T09:00:00+01:00`, { recurring: true }));
    assert.deepEqual(ids(apply([row("a1", "2026-11-05T15:00:00+01:00", "2026-11-05T16:00:00+01:00")], [op("delete", "a1")], ctx())), []);
    assert.equal(apply(s, [op("delete", "s1_20261109T0800", {}, { scope: "this" })], ctx()).length, 2);
    assert.deepEqual(ids(apply(s, [op("delete", "s1_20261109T0800", {}, { scope: "following" })], ctx())), ["s1_20261102T0800"]);
    assert.deepEqual(apply(s, [op("delete", "s1_20261109T0800", {}, { scope: "all" })], ctx()), []);
  });
  test("anlegen, dann verschieben: die Vorschau zeigt die neue Zeit", () => {
    const out = apply([], [op("create", "local-x", { title: "T", start: "2026-11-05T10:00" }), op("update", "local-x", { start: "2026-11-06T11:00" })], ctx());
    assert.equal(out[0].start, "2026-11-06T11:00:00+01:00");
  });
  test("Vorschau ändert die Eingabe nicht und ist nach einem Neustart identisch", () => {
    const rows = [row("a1", "2026-11-05T15:00:00+01:00", "2026-11-05T16:00:00+01:00")];
    const copy = JSON.stringify(rows);
    const ops = [op("update", "a1", { title: "Neu" }), op("create", "local-n", { title: "N", start: "2026-11-08T08:00" })];
    const one = apply(rows, ops, ctx()), two = apply(JSON.parse(copy), JSON.parse(JSON.stringify(ops)), ctx());
    assert.equal(JSON.stringify(rows), copy);
    assert.deepEqual(one, two);
  });
});
