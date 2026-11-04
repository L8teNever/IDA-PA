// Hochladen im Tool „Dateien“: viele Dateien und ganze Ordner, je Datei in Stücken (Cloudflare nimmt höchstens 100 MB pro Anfrage).
// Das Panel unten rechts bleibt stehen, auch wenn man in einen anderen Ordner wechselt.
import { api } from "../api.js";
import { esc, fmtBytes, openModal, sheetHead } from "../ui.js";

const CHUNK = 32 * 1024 * 1024;
const PARALLEL = 3;

let panel = null;
let items = []; // { id, name, size, sent, state: "wait"|"up"|"ok"|"skip"|"err", error, xhr }
let running = 0;
let queue = [];
let conflict = null; // Antwort für alle weiteren Dateien dieses Laufs: "replace" | "skip"
let asking = null;

const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("");

// ein Stück schicken; XHR, weil fetch keinen Fortschritt beim Senden kennt
function sendChunk(it, job, blob, offset, overwrite) {
  return new Promise((resolve, reject) => {
    const q = new URLSearchParams({ dir: job.dir, name: job.rel, upload_id: it.id, total: job.file.size, offset, overwrite });
    const x = new XMLHttpRequest();
    it.xhr = x;
    x.open("PUT", `/api/v1/fs/upload?${q}`);
    x.setRequestHeader("x-ida-upload", "1");
    x.setRequestHeader("content-type", "application/octet-stream");
    x.upload.onprogress = (e) => { it.sent = offset + e.loaded; draw(); };
    x.onload = () => {
      let d = null;
      try { d = JSON.parse(x.responseText); } catch { /* keine Antwort vom Server */ }
      if (x.status >= 200 && x.status < 300) return resolve(d);
      const err = new Error(d?.detail || `Fehler ${x.status}`);
      err.status = x.status;
      reject(err);
    };
    x.onerror = () => reject(new Error("Verbindung abgebrochen"));
    x.onabort = () => reject(new Error("Abgebrochen"));
    x.send(blob);
  });
}

function askConflict(name) {
  asking ??= new Promise((resolve) => {
    let answer = null;
    const m = openModal(`${sheetHead("Gibt es schon", "file_copy")}<p class="sheet-hint">„${esc(name)}“ liegt dort schon. Was soll mit dieser und allen weiteren Dateien passieren, die es schon gibt?</p>
      <div class="sheet-actions"><button type="button" class="btn-tonal" id="upSkip">Überspringen</button><button type="button" class="btn-danger" id="upRep">Ersetzen</button></div>`,
    { onClose: () => { asking = null; resolve(answer || "skip"); } });
    m.el.querySelector("#upSkip").onclick = () => { answer = "skip"; m.close(); };
    m.el.querySelector("#upRep").onclick = () => { answer = "replace"; m.close(); };
  });
  return asking;
}

async function uploadOne(it, job) {
  it.state = "up";
  draw();
  let overwrite = conflict === "replace";
  for (let offset = 0; ;) {
    const blob = job.file.slice(offset, offset + CHUNK);
    try {
      await sendChunk(it, job, blob, offset, overwrite);
    } catch (e) {
      if (e.status === 409 && e.message === "exists" && offset === 0) {
        conflict ??= await askConflict(it.name);
        if (conflict === "skip") { it.state = "skip"; return; }
        overwrite = true;
        continue;
      }
      throw e;
    }
    offset += blob.size;
    if (offset >= job.file.size) break;
  }
  it.sent = it.size;
  it.state = "ok";
}

async function pump() {
  while (running < PARALLEL && queue.length) {
    const job = queue.shift();
    running++;
    uploadOne(job.item, job).catch((e) => {
      job.item.state = "err";
      job.item.error = e.message;
      api.del("/fs/upload", { dir: job.dir, name: job.rel, upload_id: job.item.id }).catch(() => {});
    }).finally(() => {
      running--;
      draw();
      job.done();
      pump();
    });
  }
}

// list: [{ file, rel }] – rel ist der Pfad im Zielordner (bei Ordnern mit Unterordnern). Löst auf, wenn alles durch ist.
export function upload(list, dir) {
  if (!running && !queue.length) conflict = null;
  const started = list.map(({ file, rel }) => new Promise((done) => {
    const item = { id: newId(), name: rel, size: file.size, sent: 0, state: "wait" };
    items.push(item);
    queue.push({ item, file, rel, dir, done });
  }));
  show();
  pump();
  return Promise.all(started).then(() => list.filter(({ rel }) => items.some((x) => x.name === rel && x.state === "ok")).map(({ rel }) => rel));
}

function show() {
  if (!panel) {
    panel = document.createElement("div");
    panel.className = "up-panel";
    panel.onclick = (e) => {
      if (e.target.closest("[data-up-close]")) { items = items.filter((x) => x.state === "up" || x.state === "wait"); draw(); }
      if (e.target.closest("[data-up-stop]")) { queue.forEach((j) => { j.item.state = "skip"; j.done(); }); queue = []; items.forEach((x) => x.state === "up" && x.xhr?.abort()); }
      if (e.target.closest("[data-up-fold]")) panel.classList.toggle("fold");
    };
    document.body.appendChild(panel);
  }
  draw();
}

let drawn = 0;
function draw() {
  if (!panel) return;
  const now = performance.now();
  const busy = items.some((x) => x.state === "up" || x.state === "wait");
  if (busy && now - drawn < 100) return;
  drawn = now;
  if (!items.length) { panel.hidden = true; return; }
  panel.hidden = false;
  const total = items.reduce((a, x) => a + x.size, 0) || 1, sent = items.reduce((a, x) => a + (x.state === "skip" ? x.size : x.sent), 0);
  const failed = items.filter((x) => x.state === "err").length, ok = items.filter((x) => x.state === "ok").length;
  const head = busy ? `${ok} von ${items.length} · ${fmtBytes(sent)} von ${fmtBytes(total)}` : failed ? `${failed} fehlgeschlagen, ${ok} hochgeladen` : `${ok} hochgeladen`;
  const ic = { wait: "schedule", up: "progress_activity", ok: "check_circle", skip: "skip_next", err: "error" };
  panel.innerHTML = `<div class="up-head"><button type="button" class="up-title" data-up-fold><span class="ms ${busy ? "spin" : ""}">${busy ? "progress_activity" : failed ? "error" : "check_circle"}</span><span>${esc(head)}</span></button>
      ${busy ? `<button type="button" class="icon-btn sm" data-up-stop title="Abbrechen" aria-label="Abbrechen"><span class="ms">stop</span></button>` : `<button type="button" class="icon-btn sm" data-up-close aria-label="Schließen"><span class="ms">close</span></button>`}</div>
    <div class="up-bar"><i style="width:${Math.min(100, (sent / total) * 100)}%"></i></div>
    <div class="up-list">${items.slice(-60).reverse().map((x) => `<div class="up-row ${x.state}"><span class="ms ${x.state === "up" ? "spin" : ""}">${ic[x.state]}</span>
      <span class="up-name" title="${esc(x.name)}">${esc(x.name)}</span><small>${x.state === "err" ? esc(x.error) : x.state === "up" ? `${Math.round((x.sent / (x.size || 1)) * 100)} %` : fmtBytes(x.size)}</small></div>`).join("")}</div>`;
}

// Hineingezogenes: Dateien und ganze Ordner mit Unterordnern
async function walk(entry, prefix, out) {
  if (entry.isFile) {
    out.push({ file: await new Promise((res, rej) => entry.file(res, rej)), rel: prefix + entry.name });
    return;
  }
  const reader = entry.createReader();
  for (;;) {
    const batch = await new Promise((res, rej) => reader.readEntries(res, rej)); // liefert nur Teile, bis es leer ist
    if (!batch.length) break;
    for (const e of batch) await walk(e, `${prefix}${entry.name}/`, out);
  }
}

export async function droppedFiles(dt) {
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) return [...dt.files].map((file) => ({ file, rel: file.name }));
  const out = [];
  for (const e of entries) await walk(e, "", out);
  return out;
}
