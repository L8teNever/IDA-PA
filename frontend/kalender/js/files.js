// Anhänge, die noch auf den Abgleich warten: Datei (Blob) im Store `files`, dazu ein Outbox-Eintrag „upload“.
// Reine Logik + Speicher (db.js), kein DOM – testbar in Node.
import * as db from "./db.js";
import * as outbox from "./outbox.js";

export const MAX_FILE = 20 * 1024 * 1024;      // wie der Server
export const MAX_TOTAL = 100 * 1024 * 1024;    // alles, was offline wartet

export const total = async () => (await db.all("files")).reduce((n, f) => n + (f.size || 0), 0);

/** Datei für einen Termin (auch einen, der noch nicht auf dem Server ist) vormerken. Wirft bei zu großen Dateien. */
export async function stage(target, file) {
  if (file.size > MAX_FILE) throw new Error(`„${file.name}“ ist zu groß (höchstens 20 MB)`);
  if ((await total()) + file.size > MAX_TOTAL) throw new Error("Offline ist nur Platz für 100 MB Anhänge – erst abgleichen");
  const opId = outbox.uuid();
  await db.put("files", { opId, target, name: file.name || "datei", size: file.size, type: file.type || "application/octet-stream", blob: file, at: Date.now() });
  const e = await outbox.add({ type: "upload", target, opId, title: file.name || "Datei", body: {} });
  if (!e) await db.del("files", opId);
  return opId;
}

export const get = (opId) => db.get("files", opId);
export const drop = (opId) => db.del("files", opId);

/** Anhänge, die für diesen Termin noch warten (für Detail und Editor). */
export async function waiting(eventId) {
  const list = (await db.all("files")).filter((f) => outbox.eventOf(f.target) === eventId);
  return list.sort((a, b) => a.at - b.at).map(({ blob, ...f }) => f);
}

/** Aufräumen: Dateien, deren Outbox-Eintrag es nicht mehr gibt (z. B. Termin wieder gelöscht). */
export async function prune() {
  const keep = new Set((await outbox.all()).filter((e) => e.type === "upload").map((e) => e.opId));
  let n = 0;
  for (const f of await db.all("files")) if (!keep.has(f.opId)) { await db.del("files", f.opId); n++; }
  return n;
}

/** Einen noch wartenden Anhang zurücknehmen. */
export async function cancel(opId) {
  const e = (await outbox.all()).find((x) => x.type === "upload" && x.opId === opId);
  if (e && e.state !== "sending") await outbox.remove(e.seq);
  await db.del("files", opId);
}
