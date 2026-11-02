// Entwurf: was im Eingabefeld steht und noch nicht abgeschickt ist, liegt auf dem Server (pro Thread bzw. pro
// „neuer Thread“ im Projekt). App zu und wieder auf, oder ein anderes Gerät: der Text ist da. Läuft der Thread auf
// zwei Geräten gleichzeitig, kommt Getipptes vom anderen Gerät nach wenigen Sekunden an – aber nur, wenn hier gerade
// nichts Ungespeichertes im Feld steht (eigene Eingaben werden nie überschrieben).
import { api } from "./api.js";
import { threadKey } from "./chat.js";
import { $ } from "./ui.js";

const url = (k) => `/code/drafts/${encodeURIComponent(k)}`;
const SAVE_MS = 600, PULL_MS = 4000;

let key = null;     // Thread, dessen Entwurf im Feld steht
let synced = "";    // Stand, der sicher auf dem Server liegt
let pending = null; // getippt, aber noch nicht gespeichert: { key, text }
let timer = null;
let loading = null; // Schlüssel, der gerade geladen wird

const input = () => $("input");

function setInput(text) {
  const el = input();
  if (!el) return;
  el.value = text;
  el.dispatchEvent(new CustomEvent("input", { detail: { draft: true } })); // Höhe und Senden-Knopf anpassen
}

async function save(k, text, keepalive = false) {
  if (!k) return;
  try {
    if (keepalive) {
      // Seite wird geschlossen: fetch mit keepalive kommt auch noch an, wenn die Seite schon weg ist
      await fetch(`/api/v1${url(k)}`, { method: "PUT", keepalive: true, credentials: "same-origin",
        headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
    } else {
      await api.put(url(k), { text });
    }
    if (k === key && pending?.text === text) { synced = text; pending = null; }
    else if (k === key && !pending) synced = text;
  } catch { /* nächste Eingabe oder nächster Abgleich versucht es wieder */ }
}

// sofort speichern, was noch wartet (Thread-Wechsel, App geht in den Hintergrund)
export function flush(keepalive = false) {
  clearTimeout(timer);
  timer = null;
  if (pending) {
    const p = pending;
    if (p.key !== key) pending = null;
    return save(p.key, p.text, keepalive);
  }
}

// Text vom Server holen; ersetzt das Feld nur, wenn hier nichts Eigenes ungespeichert ist
async function pull() {
  const k = key;
  if (pending && !timer) return flush(); // letztes Speichern ging schief: nochmal
  if (!k || !input() || pending || loading) return;
  let d;
  try { d = await api.get(url(k)); } catch { return; }
  if (k !== key || pending || !input()) return;
  if (d.text !== synced && input().value === synced) { synced = d.text; setInput(d.text); }
}

// Thread geöffnet (oder nach Verbindungsabbruch neu geladen)
async function attach() {
  const k = threadKey();
  if (k === key) return pull();
  await flush();
  key = k;
  synced = "";
  pending = null;
  if (!k) return;
  loading = k;
  let d = null;
  try { d = await api.get(url(k)); } catch { /* ohne Server bleibt das Feld, wie es ist */ }
  if (loading === k) loading = null;
  if (key !== k || !d) return;
  const el = input();
  if (!el) return;
  synced = d.text;
  if (!el.value.trim()) setInput(d.text);
  else if (el.value !== d.text) changed(); // schon getippt, bevor der Entwurf da war: das Getippte gilt
}

function changed() {
  const el = input();
  if (!key || !el) return;
  pending = { key, text: el.value };
  clearTimeout(timer);
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
  synced = "";
  if (key) save(key, "");
}

// Text an den Entwurf eines (anderen) Threads anhängen – z. B. was noch in der Warteschlange stand
export async function appendDraft(k, text, keepalive = false) {
  if (!k || !text) return;
  if (k === key && input()) {
    const el = input();
    setInput(el.value.trim() ? `${el.value}\n\n${text}` : text);
    changed();
    return flush(keepalive);
  }
  let old = "";
  if (!keepalive) { try { old = (await api.get(url(k))).text; } catch { /* dann eben nur das Neue */ } }
  return save(k, old.trim() ? `${old}\n\n${text}` : text, keepalive);
}

document.addEventListener("code:mounted", attach);
// neuer Thread hat seine ID bekommen: ab jetzt gehört der Entwurf zum Thread
document.addEventListener("code:thread", (e) => {
  if (key !== `p-${e.detail.project}`) return;
  flush();
  key = `t-${e.detail.id}`;
  synced = "";
  if (input()?.value.trim()) changed();
});
document.addEventListener("visibilitychange", () => (document.hidden ? flush(true) : pull()));
addEventListener("pagehide", () => flush(true));
setInterval(() => { if (!document.hidden) pull(); }, PULL_MS);
