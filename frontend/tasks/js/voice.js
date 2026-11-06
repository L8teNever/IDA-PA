// Sprechen mit Stella im Aufgaben-Modus. Gleiche Oberfläche und gleiches Protokoll wie die Live-App (frontend/live/live.js):
// Die App holt sich beim eigenen Server ein Ticket (eine Minute, einmal nutzbar) und verbindet sich damit direkt mit dem
// Voice-Dienst (/live?ticket=…&mode=tasks). Dort hat Stella einen eigenen Aufgaben-Prompt und nur Aufgaben, Kalender,
// Gedächtnis und Wiki als Tools. Legt sie etwas an, kommt es über den Live-Stream sofort in die Liste.
import { connected, disconnected, hungUp, unlockTones } from "./tones.js";
import { follow, outputMeter } from "./meter.js";

const AWAY_MS = 30000;   // so lange darf die App im Hintergrund sein, dann legt sie auf (Mikro, Verbindung, Audio aus)
const FRAME_MS = 33;
const WORKLET = `
class Down16k extends AudioWorkletProcessor {
  constructor() { super(); this.step = sampleRate / 16000; this.pos = 0; this.acc = 0; this.n = 0; this.buf = new Int16Array(1280); this.len = 0; this.muted = false;
    this.port.onmessage = (e) => { this.muted = !!e.data.muted; }; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += this.muted ? 0 : ch[i]; this.n++; this.pos += 1;
      if (this.pos >= this.step) {
        this.pos -= this.step;
        const v = Math.max(-1, Math.min(1, this.acc / this.n));
        this.buf[this.len++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        this.acc = 0; this.n = 0;
        if (this.len === this.buf.length) { this.port.postMessage(this.buf.buffer.slice(0)); this.len = 0; }
      }
    }
    return true;
  }
}
registerProcessor("down16k", Down16k);`;
const TOOLS = { tasks_list: "schaut in die Aufgaben", tasks_create: "legt an", tasks_update: "ändert", tasks_done: "hakt ab", tasks_delete: "löscht",
                wissen_suchen: "schaut im Gedächtnis nach" };

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const rms = (a) => { let s = 0; for (let i = 0; i < a.length; i += 4) s += a[i] * a[i]; return Math.min(1, Math.sqrt(s / Math.max(1, a.length / 4)) / 6000); };
const buddy = `<div class="buddy" aria-hidden="true"><div class="face"><div class="eyes"><div class="eye"></div><div class="eye"></div></div></div><div class="props"></div></div>`;

let cur = null;

export function openVoice({ onChange } = {}) {
  if (cur) return;
  unlockTones();
  let ctx = null;
  try { ctx = new (window.AudioContext || window.webkitAudioContext)(); ctx.resume?.(); } catch { ctx = null; }
  start(ctx, onChange || (() => {}));
}

function root() { return document.getElementById("voice"); }

function view(inner) {
  let el = root();
  if (!el) { el = document.createElement("div"); el.id = "voice"; document.body.appendChild(el); }
  document.body.classList.add("voice-open");
  el.innerHTML = `<div class="live live-b"><div class="lv-buddy-wrap" id="lvBuddyWrap" data-state="idle">${buddy}</div>${inner}</div>`;
  el.querySelector(".live-b").addEventListener("touchmove", (e) => { if (!e.target.closest(".lv-chat")) e.preventDefault(); }, { passive: false });
}

function closeView() {
  root()?.remove();
  document.body.classList.remove("voice-open");
}

function draw(c) { const w = $("lvBuddyWrap"); if (w) w.dataset.state = c.muted && c.state === "listen" ? "muted" : c.state; }
const setState = (c, st) => { c.state = st; draw(c); };

function addLine(c, who, piece) {
  const box = $("lvChat");
  if (!box) return;
  let last = box.lastElementChild;
  if (!last || last.dataset.who !== who) {
    last = document.createElement("div");
    last.className = `lv-msg ${who === "user" ? "user" : "bot"}`;
    last.dataset.who = who;
    last.innerHTML = `<small>${{ user: "Du", ida_frage: "Stella fragt Ida", ida: "Ida" }[who] || esc(c.name)}</small><span class="lv-text"></span>`;
    box.appendChild(last);
  }
  last.querySelector(".lv-text").textContent += piece;
  box.scrollTop = box.scrollHeight;
}

function play(c, buf) {
  const pcm = new Int16Array(buf);
  if (!pcm.length) return;
  const f = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 0x8000;
  const ab = c.ctx.createBuffer(1, f.length, c.rate);
  ab.copyToChannel(f, 0);
  const src = c.ctx.createBufferSource();
  src.buffer = ab;
  src.connect(c.meter.node);
  const at = Math.max(c.ctx.currentTime + 0.02, c.next);
  src.start(at);
  c.next = at + ab.duration;
  c.sources.add(src);
  src.onended = () => { c.sources.delete(src); if (!c.sources.size && !c.closing && c.state === "speak") setState(c, "listen"); };
  if (c.state !== "speak") setState(c, "speak");
  animate(c);
}

function flush(c) {
  for (const s of c.sources) { try { s.stop(); } catch { /* schon gestoppt */ } }
  c.sources.clear();
  c.next = 0;
}

// Maskottchen bewegt sich nur, solange jemand spricht – in der Stille zeichnet das Handy nichts neu (Akku)
function animate(c) {
  if (c.anim || c.closed || document.hidden) return;
  $("lvBuddyWrap")?.removeAttribute("data-calm");
  c.anim = setTimeout(() => frame(c), 0);
}

function frame(c) {
  c.anim = 0;
  const wrap = $("lvBuddyWrap");
  if (c.closed || !wrap || document.hidden) return;
  const speak = c.state === "speak";
  const lvl = speak ? c.meter.level() : c.state === "listen" && !c.muted ? c.inLevel : 0;
  c.shown = follow(c.shown, lvl, speak);
  wrap.style.setProperty("--lvl", c.shown.toFixed(3));
  c.inLevel *= 0.88;
  if (speak || lvl > 0.01 || c.shown > 0.01) c.anim = setTimeout(() => frame(c), FRAME_MS);
  else { c.shown = 0; wrap.style.setProperty("--lvl", "0"); wrap.setAttribute("data-calm", ""); }
}

function clock(c, on) {
  clearInterval(c.clock);
  c.clock = 0;
  if (!on || !c.t0) return;
  const tick = () => {
    const ms = Date.now() - c.t0;
    if ($("lvTime")) $("lvTime").textContent = fmt(ms);
    const wrap = $("lvBuddyWrap");
    if (wrap && Math.floor(ms / 1000) % 4 === 0 && (c.state === "listen" || c.state === "speak") && !c.muted) {
      wrap.setAttribute("data-blink", "");
      setTimeout(() => wrap.removeAttribute("data-blink"), 320);
    }
  };
  tick();
  c.clock = setInterval(tick, 1000);
}

async function wake(c) {
  if (!("wakeLock" in navigator)) return;
  try { c.lock = await navigator.wakeLock.request("screen"); if (c.closed) c.lock.release().catch(() => {}); } catch { /* nur über HTTPS */ }
}

async function start(ctx, onChange) {
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode || !ctx) {
    return ended(null, "Dieser Browser kann kein Live-Gespräch.");
  }
  const c = { name: "Stella", sources: new Set(), next: 0, rate: 24000, closed: false, closing: false, state: "connect",
              inLevel: 0, shown: 0, t0: 0, muted: false, ctx, onChange, changed: false };
  cur = c;
  view(`<div class="lv-status lv-corner"><span class="lv-time" id="lvTime">0:00</span></div>
    <div class="lv-tool" id="lvTool" hidden><span class="ms">checklist</span><span id="lvToolText"></span></div>
    <div class="lv-chat" id="lvChat" aria-live="polite"></div>
    <div class="lv-btns"><button class="es-btn" id="lvMute"><span class="ms">mic</span>Stumm</button>
      <button class="es-btn lv-end" id="lvEnd"><span class="ms">call_end</span>Beenden</button></div>`);
  const r = root().querySelector(".live-b");
  r.classList.add("intro");
  const props = r.querySelector(".buddy .props");
  r.dataset.mood = "sleep";
  if (props) props.innerHTML = `<b class="z z1">z</b><b class="z z2">z</b><b class="z z3">Z</b>`;
  setTimeout(() => {
    delete r.dataset.mood;
    if (props) props.innerHTML = "";
    r.classList.add("waking");
    setTimeout(() => r.classList.remove("waking"), 800);
  }, 1600);
  c.introEnd = Date.now() + 2400;
  c.endIntro = () => setTimeout(() => r.classList.remove("intro"), Math.max(0, c.introEnd - Date.now()));
  $("lvEnd").onclick = () => stop(c);
  $("lvMute").onclick = () => {
    c.muted = !c.muted;
    c.node?.port.postMessage({ muted: c.muted });
    $("lvMute").innerHTML = c.muted ? `<span class="ms">mic_off</span>Ton an` : `<span class="ms">mic</span>Stumm`;
    $("lvMute").classList.toggle("primary", c.muted);
    draw(c);
  };
  draw(c);
  $("lvBuddyWrap").setAttribute("data-calm", "");
  c.meter = outputMeter(c.ctx);
  c.onVisible = () => {
    if (document.hidden) { clock(c, false); c.away = setTimeout(() => stop(c, "", "Beendet, weil die App im Hintergrund war."), AWAY_MS); return; }
    clearTimeout(c.away);
    clock(c, true);
    wake(c);
    animate(c);
  };
  document.addEventListener("visibilitychange", c.onVisible);
  wake(c);
  try {
    const [stream, t] = await Promise.all([
      navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } }),
      fetch("/api/v1/voice/ticket", { method: "POST", credentials: "same-origin" }).then(async (res) => {
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Sprechen geht gerade nicht");
        return res.json();
      }),
    ]);
    c.stream = stream;
    if (c.closed) return stream.getTracks().forEach((x) => x.stop());
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
    await c.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    c.ws = new WebSocket(`${t.url}?ticket=${encodeURIComponent(t.ticket)}&mode=${encodeURIComponent(t.mode || "tasks")}`);
    c.ws.binaryType = "arraybuffer";
    c.ws.onmessage = (e) => {
      if (typeof e.data !== "string") return play(c, e.data);
      const m = JSON.parse(e.data);
      if (m.type === "ready") {
        c.rate = m.rate || 24000;
        c.t0 = Date.now();
        if (!document.hidden) clock(c, true);
        const src = c.ctx.createMediaStreamSource(stream);
        c.node = new AudioWorkletNode(c.ctx, "down16k");
        c.node.port.onmessage = (ev) => {
          if (!c.muted) { c.inLevel = Math.max(c.inLevel, rms(new Int16Array(ev.data))); if (c.inLevel > 0.05) animate(c); }
          if (c.ws.readyState === 1) c.ws.send(ev.data);
        };
        src.connect(c.node);
        setState(c, "listen");
        connected();
        c.endIntro();
      } else if (m.type === "text") addLine(c, m.who, m.text);
      else if (m.type === "interrupted") { flush(c); setState(c, "listen"); }
      else if (m.type === "tool") {
        setState(c, m.running ? "tool" : "listen");
        $("lvTool").hidden = !m.running;
        if (m.running) $("lvToolText").textContent = TOOLS[m.name] ? `${TOOLS[m.name]} …` : "schaut nach …";
        else if (/^tasks_/.test(m.name || "")) { c.changed = true; c.onChange(); }
      } else if (m.type === "error") stop(c, m.message || "Das Gespräch ist abgebrochen");
      else if (m.type === "end") { c.closing = true; setTimeout(() => stop(c), Math.max(0, (c.next - c.ctx.currentTime) * 1000) + 300); }
    };
    c.ws.onclose = () => { if (!c.closing) stop(c, c.closed ? "" : "Verbindung zu Stella getrennt"); };
  } catch (e) {
    stop(c, e?.name === "NotAllowedError" ? "Kein Zugriff aufs Mikrofon – bitte im Browser erlauben" : e?.message || "Sprechen geht gerade nicht");
  }
}

async function stop(c, problem, note) {
  if (!c || c.closed) return;
  c.closed = true;
  setTimeout(problem ? disconnected : hungUp, 250);
  try { c.ws?.readyState === 1 && c.ws.send(JSON.stringify({ type: "end" })); } catch { /* zu */ }
  try { c.ws?.close(); } catch { /* zu */ }
  c.stream?.getTracks().forEach((t) => t.stop());
  clock(c, false);
  clearTimeout(c.anim);
  clearTimeout(c.away);
  document.removeEventListener("visibilitychange", c.onVisible);
  c.lock?.release().catch(() => {});
  try { await c.ctx?.close(); } catch { /* zu */ }
  if (cur === c) cur = null;
  c.onChange();
  if (problem || note) ended(c, problem || note);
  else closeView();
}

// Fehler oder Hinweis: kurz anzeigen, Knopf zurück zur Liste
function ended(c, msg) {
  cur = null;
  view(`<div class="lv-status"><span class="lv-pill fail"><span class="ms">error</span>${esc(msg)}</span></div>
    <div class="lv-chat"></div>
    <div class="lv-btns"><button class="es-btn" id="lvBack"><span class="ms">arrow_back</span>Zurück zu den Aufgaben</button>
      <button class="es-btn primary" id="lvAgain"><span class="ms">graphic_eq</span>Nochmal</button></div>`);
  $("lvBack").onclick = closeView;
  $("lvAgain").onclick = () => { closeView(); openVoice({ onChange: c?.onChange }); };
}

addEventListener("pagehide", () => { if (cur) stop(cur); });
