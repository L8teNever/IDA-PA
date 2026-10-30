import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";
import * as db from "../js/db.js";
import * as ob from "../js/outbox.js";
import * as files from "../js/files.js";

beforeEach(() => db.setBackend(db.memBackend()));
const file = (name, size = 10) => ({ name, size, type: "application/pdf" });
const create = (id) => ob.add({ type: "create", target: id, body: { title: "Mit Anhang" }, scope: "all", title: "Mit Anhang" });

describe("Anhänge offline", () => {
  test("stage legt Datei und Outbox-Eintrag an", async () => {
    const id = ob.newLocalId();
    await create(id);
    const opId = await files.stage(id, file("Plan.pdf"));
    const l = await ob.all();
    assert.deepEqual(l.map((e) => e.type), ["create", "upload"]);
    assert.equal(l[1].opId, opId);
    assert.equal((await files.get(opId)).name, "Plan.pdf");
    assert.deepEqual((await files.waiting(id)).map((f) => f.name), ["Plan.pdf"]);
  });
  test("nach dem Anlegen zeigt remap auch den Anhang auf die echte ID", async () => {
    const id = ob.newLocalId();
    await create(id);
    await files.stage(id, file("a.pdf"));
    await ob.remap(id, "abc123", 0);
    assert.equal((await ob.all())[1].target, "abc123");
    assert.deepEqual((await files.waiting("abc123")).map((f) => f.name), ["a.pdf"]);
  });
  test("anlegen + löschen räumt auch die Datei weg", async () => {
    const id = ob.newLocalId();
    await create(id);
    const opId = await files.stage(id, file("weg.pdf"));
    await ob.add({ type: "delete", target: id, body: {}, scope: "all" });
    assert.equal(await ob.count(), 0);
    assert.equal(await files.get(opId), undefined);
  });
  test("cancel nimmt einen wartenden Anhang zurück", async () => {
    const id = ob.newLocalId();
    await create(id);
    const opId = await files.stage(id, file("x.pdf"));
    await files.cancel(opId);
    assert.deepEqual((await ob.all()).map((e) => e.type), ["create"]);
    assert.equal(await files.get(opId), undefined);
  });
  test("zu große Dateien und zu viel Gesamtgröße werden abgelehnt", async () => {
    await assert.rejects(() => files.stage("t1", file("riesig.bin", files.MAX_FILE + 1)), /zu groß/);
    for (let i = 0; i < 5; i++) await files.stage("t1", file(`f${i}.bin`, files.MAX_FILE));
    await assert.rejects(() => files.stage("t1", file("noch.bin", files.MAX_FILE)), /100 MB/);
  });
  test("prune entfernt Dateien ohne Outbox-Eintrag", async () => {
    await files.stage("t2", file("a.pdf"));
    await db.put("files", { opId: "verwaist", target: "t3", name: "v.pdf", size: 1, blob: {}, at: 1 });
    assert.equal(await files.prune(), 1);
    assert.equal((await db.all("files")).length, 1);
  });
});

describe("Schema 3", () => {
  test("Migration von 2 auf 3 kopiert den alten Spiegel nicht noch einmal", async () => {
    const be = db.memBackend();
    await be.put("meta", 2, "schema");
    await be.put("kv", { at: 1, from: "2026-01-01", to: "2026-02-01", events: [{ id: "a", start: "2026-01-05T10:00:00+01:00", end: "2026-01-05T11:00:00+01:00" }] }, "window");
    const r = await db.migrate(be);
    assert.equal(r.migrated, true);
    assert.equal(r.occ, 0);
    assert.equal(await be.get("meta", "schema"), db.SCHEMA);
  });
});
