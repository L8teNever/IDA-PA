import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";
import * as db from "../js/db.js";
import * as ob from "../js/outbox.js";

beforeEach(() => db.setBackend(db.memBackend()));
const op = (type, target, body = {}, extra = {}) => ({ type, target, body, scope: "all", baseSequence: 0, title: body.title || "", ...extra });
const types = async () => (await ob.all()).map((e) => `${e.type}:${e.target}`);

describe("Zusammenfassen (coalesce)", () => {
  test("anlegen + ändern = ein Anlegen mit den neuen Werten", async () => {
    const id = ob.newLocalId();
    await ob.add(op("create", id, { title: "Zahnarzt", start: "2026-11-05T10:00" }));
    await ob.add(op("update", id, { title: "Zahnarzt Dr. Müller", location: "Praxis" }));
    const l = await ob.all();
    assert.equal(l.length, 1);
    assert.deepEqual([l[0].type, l[0].body.title, l[0].body.location, l[0].body.start], ["create", "Zahnarzt Dr. Müller", "Praxis", "2026-11-05T10:00"]);
  });
  test("anlegen + löschen = nichts, auch wenn dazwischen geändert wurde", async () => {
    const id = ob.newLocalId();
    await ob.add(op("create", id, { title: "Flüchtig" }));
    await ob.add(op("update", id, { location: "x" }));
    const r = await ob.add(op("delete", id));
    assert.equal(await ob.count(), 0);
    assert.equal(r, null);
  });
  test("ändern + ändern (gleiches Ziel, gleicher scope) = eine Änderung mit Basis des ersten", async () => {
    await ob.add(op("update", "abc123", { title: "A" }, { baseSequence: 3 }));
    await ob.add(op("update", "abc123", { location: "B" }, { baseSequence: 4 }));
    const l = await ob.all();
    assert.equal(l.length, 1);
    assert.deepEqual([l[0].body.title, l[0].body.location, l[0].baseSequence], ["A", "B", 3]);
  });
  test("ändern + löschen = löschen mit Basis der Änderung", async () => {
    await ob.add(op("update", "abc123", { title: "A" }, { baseSequence: 3 }));
    await ob.add(op("delete", "abc123", {}, { baseSequence: 4 }));
    const l = await ob.all();
    assert.deepEqual(l.map((e) => [e.type, e.baseSequence]), [["delete", 3]]);
  });
  test("verschiedene scopes und Vorkommen werden nicht zusammengefasst, die Reihenfolge bleibt", async () => {
    await ob.add(op("update", "s1_20261105T1000", { title: "nur dieser" }, { scope: "this" }));
    await ob.add(op("update", "s1_20261112T1000", { title: "folgende" }, { scope: "following" }));
    await ob.add(op("update", "s1_20261112T1000", { title: "folgende 2" }, { scope: "following" }));
    assert.deepEqual(await types(), ["update:s1_20261105T1000", "update:s1_20261112T1000"]);
    assert.equal((await ob.all())[1].body.title, "folgende 2");
  });
  test("andere Termine stören sich nicht", async () => {
    await ob.add(op("update", "a1", { title: "1" }));
    await ob.add(op("update", "b1", { title: "2" }));
    await ob.add(op("update", "a1", { location: "3" }));
    const l = await ob.all();
    assert.equal(l.length, 2);
    assert.deepEqual(l.map((e) => e.body), [{ title: "1", location: "3" }, { title: "2" }]);
  });
  test("ein gerade gesendeter Eintrag wird nie angefasst", async () => {
    const e = await ob.add(op("update", "a1", { title: "1" }));
    await ob.mark(e.seq, { state: "sending" });
    await ob.add(op("update", "a1", { location: "x" }));
    assert.equal((await ob.all()).length, 2);
  });
  test("löschen + löschen = ein Löschen", async () => {
    await ob.add(op("delete", "a1"));
    await ob.add(op("delete", "a1"));
    assert.equal(await ob.count(), 1);
  });
  test("coalesce ist rein: verändert die Eingabe nicht", () => {
    const list = [{ seq: 1, type: "update", target: "a1", scope: "all", state: "pending", body: { title: "x" } }];
    const copy = JSON.stringify(list);
    ob.coalesce(list, op("update", "a1", { title: "y" }));
    assert.equal(JSON.stringify(list), copy);
  });
});

describe("Speicher und Nummern", () => {
  test("seq steigt, pending sortiert, nextTry wird beachtet, mark/remove", async () => {
    const a = await ob.add(op("update", "a1", { title: "1" })), b = await ob.add(op("update", "b1", { title: "2" }));
    assert.ok(b.seq > a.seq);
    await ob.mark(a.seq, { nextTry: Date.now() + 60000, tries: 1 });
    assert.deepEqual((await ob.pending()).map((e) => e.target), ["b1"]);
    assert.equal((await ob.pending(Date.now() + 120000)).length, 2);
    await ob.remove(b.seq);
    assert.equal(await ob.count(), 1);
    const s = await ob.summary();
    assert.deepEqual([s.total, s.pending, s.conflict], [1, 1, 0]);
  });
  test("backoff: 5 s · 3^n, höchstens 15 Minuten", () => {
    assert.deepEqual([1, 2, 3, 4, 5, 9].map(ob.backoff), [5000, 15000, 45000, 135000, 405000, 900000]);
  });
  test("lokale IDs haben keinen Unterstrich; vor dem ersten „_“ steht die Termin-ID", () => {
    const id = ob.newLocalId();
    assert.ok(ob.isLocal(id) && !id.includes("_"));
    assert.equal(ob.eventOf(`${id}_20261105T1000`), id);
    assert.equal(ob.eventOf("abc123"), "abc123");
  });
  test("viele Einträge sind schnell (1000 in unter 2 s)", async () => {
    const t = Date.now();
    for (let i = 0; i < 1000; i++) await db.put("outbox", { type: "update", target: `e${i}`, state: "pending", scope: "all", body: {} });
    assert.equal((await ob.pending()).length, 1000);
    assert.ok(Date.now() - t < 2000);
  });
});

describe("Lokale ID → echte ID", () => {
  test("remap schreibt Ziele aller Einträge um und setzt die Basis-Sequenz der folgenden", async () => {
    const id = ob.newLocalId();
    await ob.add(op("create", id, { title: "Serie", repeat: "weekly" }));
    await ob.add(op("update", `${id}_20261105T1000`, { title: "nur dieser" }, { scope: "this" }));
    await ob.add(op("update", `${id}_20261112T1000`, { title: "folgende" }, { scope: "following" }));
    await ob.remap(id, "k3x9a2b7q1zv", 4);
    const l = await ob.all();
    assert.deepEqual(l.map((e) => e.target), ["k3x9a2b7q1zv", "k3x9a2b7q1zv_20261105T1000", "k3x9a2b7q1zv_20261112T1000"]);
    assert.deepEqual(l.slice(1).map((e) => e.baseSequence), [4, 4]);
  });
  test("protectedEvents nennt alle Termine mit offenen Änderungen", async () => {
    await ob.add(op("update", "a1_20261105T1000", { title: "x" }, { scope: "this" }));
    await ob.add(op("delete", "b2"));
    assert.deepEqual([...(await ob.protectedEvents())].sort(), ["a1", "b2"]);
  });
});

describe("Reihenfolge beim Senden (nextDue)", () => {
  test("wartet das Anlegen noch (Wartezeit), kommt der Anhang dahinter nicht vor", async () => {
    const id = ob.newLocalId();
    const c = await ob.add(op("create", id, { title: "A" }));
    await ob.add(op("upload", id, {}, { opId: "up1" }));
    await ob.mark(c.seq, { nextTry: Date.now() + 60000 });
    assert.equal(await ob.nextDue(), null);
    await ob.mark(c.seq, { nextTry: 0 });
    assert.equal((await ob.nextDue()).type, "create");
  });
  test("ein Konflikt hält nur die Einträge desselben Termins auf", async () => {
    const a = (await ob.add(op("update", "t1", { title: "x" }))).seq;
    await ob.add(op("upload", "t1", {}, { opId: "u" }));
    await ob.add(op("update", "t2", { title: "y" }));
    await ob.mark(a, { state: "conflict" });
    assert.equal((await ob.nextDue()).target, "t2");
  });
});
