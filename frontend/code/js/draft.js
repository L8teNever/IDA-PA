// Entwurf: was im Eingabefeld steht und noch nicht abgeschickt ist (Text und angehängte Dateien), liegt auf dem Server (pro Thread bzw. pro
// „neuer Thread“ im Projekt). App zu und wieder auf, oder ein anderes Gerät: der Text ist da. Läuft der Thread auf
// zwei Geräten gleichzeitig, kommt Getipptes vom anderen Gerät nach wenigen Sekunden an – aber nur, wenn hier gerade
// nichts Ungespeichertes im Feld steht (eigene Eingaben werden nie überschrieben).
import { api } from "./api.js";
import { threadKey } from "./chat.js";
import { draftFiles, restoreFiles, setFiles } from "./composer.js";
import { $ } from "./ui.js";

const url = (k) => `/code/drafts/${encodeURIComponent(k)}`;
const SAVE_MS = 600, PULL_MS = 10000;
const snap = (d) => JSON.stringify([d.text, d.files.map((f) => f.id)]);
const EMPTY = snap({ text: "", files: [] });

let key = null;     // Thread, dessen Entwurf im Feld steht
let synced = EMPTY; // Stand, der sicher auf dem Server liegt
let pending = null; // getippt, aber noch nicht gespeichert: { key, text, files }
let timer = null;
let loading = null; // Schlüssel, der gerade geladen wird

const input = () => $("input");
const current = () => ({ text: input()?.value ?? "", files: draftFiles() });

function setInput(text) {
  const el = input();
  if (!el) return;
  el.value = text;
  el.dispatchEvent(new CustomEvent("input", { detail: { draft: true } })); // Höhe und Senden-Knopf anpassen
}

async function save(k, d, keepalive = false) {
  if (!k) return;
  const body = { text: d.text, files: d.files.map((f) => f.id) };
  try {
    if (keepalive) {
      // Seite wird geschlossen: fetch mit keepalive kommt auch noch an, wenn die Seite schon weg ist
      await fetch(`/api/v1${url(k)}`, { method: "PUT", keepalive: true, credentials: "same-origin",
        headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    } else {
      await api.put(url(k), body);
    }
    if (k === key && pending && snap(pending) === snap(d)) { synced = snap(d); pending = null; }
    else if (k === key && !pending) synced = snap(d);
  } catch { /* nächste Eingabe oder nächster Abgleich versucht es wieder */ }
}

// sofort speichern, was noch wartet (Thread-Wechsel, App geht in den Hintergrund)
export function flush(keepalive = false) {
  clearTimeout(timer);
  timer = null;
  if (pending) {
    const p = pending;
    if (p.key !== key) pending = null;
    return save(p.key, p, keepalive);
  }
}

// Stand vom Server holen; ersetzt das Feld nur, wenn hier nichts Eigenes ungespeichert ist
async function pull() {
  const k = key;
  if (pending && !timer) return flush(); // letztes Speichern ging schief: nochmal
  if (!k || !input() || pending || loading) return;
  let d;
  try { d = await api.get(url(k)); } catch { return; }
  if (k !== key || pending || !input()) return;
  if (snap(d) !== synced && snap(current()) === synced) { synced = snap(d); setInput(d.text); setFiles(d.files); }
}

// Thread geöffnet (oder nach Verbindungsabbruch neu geladen)
async function attach() {
  const k = threadKey();
  if (k === key) return pull();
  await flush();
  key = k;
  synced = EMPTY;
  pending = null;
  if (!k) return;
  loading = k;
  let d = null;
  try { d = await api.get(url(k)); } catch { /* ohne Server bleibt das Feld, wie es ist */ }
  if (loading === k) loading = null;
  if (key !== k || !d) return;
  const el = input();
  if (!el) return;
  synced = snap(d);
  if (!el.value.trim()) setInput(d.text); // schon getippt, bevor der Entwurf da war: das Getippte gilt
  restoreFiles(d.files);
  changed();
}

function changed() {
  const el = input();
  if (!key || !el) return;
  clearTimeout(timer);
  timer = null;
  const d = current();
  if (snap(d) === synced) { pending = null; return; }
  pending = { key, ...d };
  timer = setTimeout(() => { timer = null; flush(); }, SAVE_MS);
}

// nach dem Zeichnen des Eingabefelds aufrufen
export function bindDraft() {
  flush();
  pending = null;
  key = null; // frisches Feld: der Entwurf wird beim Öffnen des Threads neu geladen
  input()?.addEventListener("input", (e) => { if (!e.detail?.draft) changed(); });
}

// abgeschickt: Entwurf leeren
export function draftSent() {
  clearTimeout(timer);
  timer = null;
  pending = null;
  synced = EMPTY;
  if (key) save(key, { text: "", files: [] });
}

// Text und Dateien an den Entwurf eines (anderen) Threads anhängen – z. B. was noch in der Warteschlange stand
export async function appendDraft(k, text, files = [], keepalive = false) {
  if (!k || (!text && !files.length)) return;
  if (k === key && input()) {
    const el = input();
    if (text) setInput(el.value.trim() ? `${el.value}\n\n${text}` : text);
    restoreFiles(files);
    changed();
    return flush(keepalive);
  }
  let old = { text: "", files: [] };
  if (!keepalive) { try { old = await api.get(url(k)); } catch { /* dann eben nur das Neue */ } }
  return save(k, { text: old.text.trim() && text ? `${old.text}\n\n${text}` : old.text || text, files: [...old.files, ...files] }, keepalive);
}

document.addEventListener("code:mounted", attach);
// neuer Thread hat seine ID bekommen: ab jetzt gehört der Entwurf zum Thread
document.addEventListener("code:thread", (e) => {
  if (key !== `p-${e.detail.project}`) return;
  flush();
  key = `t-${e.detail.id}`;
  synced = EMPTY;
  changed();
});
document.addEventListener("code:attach", changed);
document.addEventListener("visibilitychange", () => (document.hidden ? flush(true) : pull()));
addEventListener("pagehide", () => flush(true));
setInterval(() => { if (!document.hidden) pull(); }, PULL_MS);
