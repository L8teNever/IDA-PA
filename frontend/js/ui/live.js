// Live conversation with Stella (Gemini Live) – its own full page /live, built like the tracker timer:
// big ring in the middle (pulses with the voices, shows how long the talk runs), name + state below, buttons at the bottom.
// IDA hands out a one-minute ticket, the browser talks to the voice container directly:
// microphone -> PCM 16 kHz (AudioWorklet) -> WebSocket -> Gemini; Gemini's PCM 24 kHz comes back and is played in order.
import { api } from "../api.js";
import { canGoBack, navigate } from "../router.js";
import { agentById, colors, mainAgent, state } from "../store.js";
import { $, esc, toast } from "./dom.js";
import { setLive } from "./mascot.js";
import { setView } from "./shell.js";

// Runs in the audio thread: averages the mic signal down to 16 kHz and hands over ~40 ms Int16 blocks.
const WORKLET = `
class Down16k extends AudioWorkletProcessor {
  constructor() { super(); this.step = sampleRate / 16000; this.pos = 0; this.acc = 0; this.n = 0; this.buf = new Int16Array(640); this.len = 0; this.muted = false;
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

const RING = 2 * Math.PI * 88; // same ring as the tracker timer
const STATES = { connect: "Verbinde …", listen: "Hört zu", speak: "spricht", tool: "Schaut nach …", muted: "Stumm" };
let cur = null; // the running conversation
let gestureCtx = null; // AudioContext made in the tap on the live button (iPhone only allows sound after a tap)

const voiceAgent = () => agentById(state.boot?.voice_agent) || { name: "Stella", palette: "pink" };
const fmt = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

function paint(a) { // the page wears the voice agent's colours
  for (const [k, v] of Object.entries(colors(a))) document.documentElement.style.setProperty(k, v);
}

function rms(int16) {
  let sum = 0;
  for (let i = 0; i < int16.length; i += 4) sum += int16[i] * int16[i];
  return Math.min(1, Math.sqrt(sum / Math.max(1, int16.length / 4)) / 6000);
}

// ---------- page ----------
export async function livePage() {
  const a = voiceAgent();
  setView("page");
  document.body.classList.add("live-view");
  paint(a);
  $("page").innerHTML = `
    <div class="live live-b">
      <div class="lv-buddy-wrap" id="lvBuddyWrap" data-state="connect">
        <div class="buddy" id="lvBuddy" aria-hidden="true">
          <i class="lv-wave lv-w1"></i><i class="lv-wave lv-w2"></i><i class="lv-wave lv-w3"></i>
          <div class="face"><div class="eyes"><div class="eye"></div><div class="eye"></div></div></div>
          <div class="props"></div>
        </div>
      </div>
      <div class="lv-status" id="lvStatus">
        <span class="lv-pill" id="lvPill"><span class="ms" id="lvIcon">graphic_eq</span><span id="lvState">${STATES.connect}</span></span>
        <span class="lv-time" id="lvTime">0:00</span>
      </div>
      <div class="lv-tool" id="lvTool" hidden><span class="ms">travel_explore</span><span id="lvToolText"></span></div>
      <div class="lv-chat" id="lvChat" aria-live="polite"></div>
      <div class="lv-btns" id="lvBtns">
        <button class="es-btn" id="lvMute"><span class="ms">mic</span>Stumm</button>
        <button class="es-btn lv-end" id="lvEnd"><span class="ms">call_end</span>Auflegen</button></div>
      <div class="tmr-note" id="lvNote" hidden></div>
    </div>`;
  const el = $("page").querySelector(".live-b");
  if (el) {
    el.addEventListener("touchmove", (e) => {
      if (!e.target.closest(".lv-chat")) e.preventDefault();
    }, { passive: false });
  }
  if (!gestureCtx) { // opened directly (link, reload): the browser needs a tap before it may play sound
    $("lvBtns").innerHTML = `<button class="es-btn primary" id="lvGo"><span class="ms">graphic_eq</span>Gespräch starten</button>
      <button class="es-btn" id="lvBack"><span class="ms">close</span>Zurück</button>`;
    $("lvState").textContent = "bereit";
    $("lvBuddyWrap").dataset.state = "idle";
    $("lvGo").onclick = () => { prime(); livePage(); };
    $("lvBack").onclick = leave;
    return;
  }
  start(a);
}

function leave() {
  if (canGoBack()) history.back();
  else navigate("/");
}

function bind(c) {
  $("lvEnd").onclick = () => stop(c);
  $("lvMute").onclick = () => {
    c.muted = !c.muted;
    c.node?.port.postMessage({ muted: c.muted });
    $("lvMute").innerHTML = c.muted ? `<span class="ms">mic_off</span>Ton an` : `<span class="ms">mic</span>Stumm`;
    $("lvMute").classList.toggle("primary", c.muted);
    draw(c);
  };
  draw(c);
}

function setState(c, st) { c.state = st; draw(c); }

function draw(c) {
  const wrap = $("lvBuddyWrap");
  if (!wrap) return;
  const st = c.muted && c.state === "listen" ? "muted" : c.state;
  wrap.dataset.state = st;
  const pill = $("lvPill");
  if (pill) pill.className = `lv-pill ${st}`;
  const stateEl = $("lvState");
  if (stateEl) stateEl.textContent = st === "speak" ? `${c.name} spricht` : STATES[st] || "";
  const iconEl = $("lvIcon");
  if (iconEl) iconEl.textContent = { speak: "record_voice_over", tool: "travel_explore", muted: "mic_off" }[st] || "graphic_eq";
}

// full chat history with speaker bubbles
function addLine(who, piece, fresh = false) {
  const box = $("lvChat");
  if (!box) return;
  let last = box.lastElementChild;
  if (fresh || !last || last.dataset.who !== who) {
    last = document.createElement("div");
    last.className = `lv-msg ${who === "user" ? "user" : "bot"}`;
    last.dataset.who = who;
    last.innerHTML = `<small>${who === "user" ? "Du" : esc(cur?.name || "Stella")}</small><span class="lv-text"></span>`;
    box.appendChild(last);
  }
  last.querySelector(".lv-text").textContent += piece;
  box.scrollTop = box.scrollHeight;
}

function note(text) {
  const n = $("lvNote");
  if (n) { n.hidden = false; n.innerHTML = `<span class="ms">info</span>${esc(text)}`; }
}

// ---------- audio ----------
function prime() {
  try { gestureCtx = new (window.AudioContext || window.webkitAudioContext)(); gestureCtx.resume?.(); } catch { gestureCtx = null; }
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
  src.connect(c.ctx.destination);
  const at = Math.max(c.ctx.currentTime + 0.02, c.next);
  src.start(at);
  c.next = at + ab.duration;
  c.sources.add(src);
  c.outLevel = Math.max(c.outLevel, rms(pcm));
  src.onended = () => {
    c.sources.delete(src);
    if (!c.sources.size && !c.closing && c.state === "speak") setState(c, "listen");
  };
  if (c.state !== "speak") setState(c, "speak");
}

function flush(c) { // interrupted: drop what was still queued
  for (const s of c.sources) { try { s.stop(); } catch { /* already stopped */ } }
  c.sources.clear();
  c.next = 0;
}

// mascot + clock: clock runs from "ready", waves breathe with whoever is talking
function tick(c) {
  if (!$("lvBuddyWrap")) { // left the page: the talk ends, the app gets its colours back
    paint(state.agent || mainAgent());
    return stop(c);
  }
  if (c.t0) $("lvTime").textContent = fmt(Date.now() - c.t0);
  const lvl = c.state === "speak" ? c.outLevel : c.state === "listen" && !c.muted ? c.inLevel : 0;
  c.shown = c.shown * 0.6 + lvl * 0.4;
  $("lvBuddyWrap").style.setProperty("--lvl", c.shown.toFixed(3));
  c.outLevel *= 0.7;
  c.inLevel *= 0.7;
}

async function wake(c) {
  if (!("wakeLock" in navigator)) return;
  try { c.lock = await navigator.wakeLock.request("screen"); } catch { /* only over HTTPS */ }
}

async function start(a) {
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) { note("Dieser Browser kann kein Live-Gespräch."); return; }
  const c = { name: a.name, sources: new Set(), next: 0, rate: 24000, closed: false, closing: false, state: "connect",
              lines: [], inLevel: 0, outLevel: 0, shown: 0, t0: 0 };
  cur = c;
  c.ctx = gestureCtx;
  gestureCtx = null;
  setLive("phone");
  bind(c);
  c.clock = setInterval(() => tick(c), 100);
  c.onVisible = () => { if (document.visibilityState === "visible") wake(c); };
  document.addEventListener("visibilitychange", c.onVisible);
  wake(c);
  try {
    const [stream, ticket] = await Promise.all([
      navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } }),
      api.post("/voice/ticket"),
    ]);
    c.stream = stream;
    if (c.closed) return stream.getTracks().forEach((t) => t.stop());
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
    await c.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    c.ws = new WebSocket(ticket.url);
    c.ws.binaryType = "arraybuffer";
    c.ws.onmessage = (e) => {
      if (typeof e.data !== "string") return play(c, e.data);
      const m = JSON.parse(e.data);
      if (m.type === "ready") {
        c.rate = m.rate || 24000;
        c.t0 = Date.now();
        const src = c.ctx.createMediaStreamSource(stream);
        c.node = new AudioWorkletNode(c.ctx, "down16k");
        c.node.port.onmessage = (ev) => {
          if (!c.muted) c.inLevel = Math.max(c.inLevel, rms(new Int16Array(ev.data)));
          if (c.ws.readyState === 1) c.ws.send(ev.data);
        };
        src.connect(c.node);
        setState(c, "listen");
        if (m.offline) note("IDA ist gerade nicht erreichbar – Stella hat keinen Zugriff auf deine Daten.");
      } else if (m.type === "text") {
        const last = c.lines[c.lines.length - 1];
        if (last && last.who === m.who) last.text += m.text; else c.lines.push({ who: m.who, text: m.text });
        addLine(m.who, m.text);
      } else if (m.type === "interrupted") { flush(c); setState(c, "listen"); }
      else if (m.type === "tool") {
        setState(c, m.running ? "tool" : "listen");
        const t = $("lvTool");
        if (t) {
          t.hidden = !m.running;
          if (m.running) $("lvToolText").textContent = m.name ? `schaut nach (${m.name}) …` : "schaut nach …";
        }
      }
      else if (m.type === "error") stop(c, m.message || "Das Gespräch ist abgebrochen");
      else if (m.type === "end") {
        c.closing = true; // let her finish the sentence, then hang up
        setTimeout(() => stop(c), Math.max(0, (c.next - c.ctx.currentTime) * 1000) + 300);
      }
    };
    c.ws.onclose = () => { if (!c.closing) stop(c, c.closed ? "" : "Verbindung zu Stella getrennt"); };
  } catch (e) {
    stop(c, e?.name === "NotAllowedError" ? "Kein Zugriff aufs Mikrofon – bitte im Browser erlauben" : e?.message || "Live-Gespräch geht gerade nicht");
  }
}

async function stop(c, problem) {
  if (!c || c.closed) return;
  c.closed = true;
  try { c.ws?.readyState === 1 && c.ws.send(JSON.stringify({ type: "end" })); } catch { /* closed */ }
  try { c.ws?.close(); } catch { /* closed */ }
  c.stream?.getTracks().forEach((t) => t.stop());
  clearInterval(c.clock);
  document.removeEventListener("visibilitychange", c.onVisible);
  c.lock?.release().catch(() => {});
  try { await c.ctx?.close(); } catch { /* closed */ }
  setLive(null);
  if (cur === c) cur = null;
  ended(c, problem);
}

// result view like the tracker after ticking: what happened, where to go next
function ended(c, problem) {
  const page = $("page").querySelector(".live");
  if (!page) { if (problem) toast(problem); return; }
  const took = c.t0 ? fmt(Date.now() - c.t0) : "";
  const wrap = $("lvBuddyWrap");
  if (wrap) wrap.dataset.state = problem ? "fail" : "done";
  const status = $("lvStatus");
  if (status) {
    status.innerHTML = problem
      ? `<span class="lv-pill fail"><span class="ms">error</span>Das hat nicht geklappt</span><span class="lv-time">${esc(problem)}</span>`
      : `<span class="lv-pill done"><span class="ms">call_end</span>Gespräch beendet</span><span class="lv-time">${took ? `${took} min` : ""}</span>`;
  }
  $("lvBtns").innerHTML = `
    <a class="es-btn primary" href="/a/${esc(state.boot?.voice_agent || "stella")}"><span class="ms">forum</span>Zu ${esc(c.name)}</a>
    <button class="es-btn" id="lvAgain"><span class="ms">graphic_eq</span>Nochmal</button>
    <a class="es-btn" href="/"><span class="ms">home</span>Startseite</a>`;
  $("lvAgain").onclick = () => { prime(); livePage(); };
}

// ---------- button in the input field ----------
export function initLive() {
  const b = $("liveBtn");
  if (!b) return;
  b.hidden = !state.boot?.voice;
  b.onclick = (e) => {
    e.preventDefault();
    prime(); // in the tap itself – before anything is awaited
    navigate("/live");
  };
  // Esc hangs up; leaving the page ends the talk too (see tick)
  addEventListener("keydown", (e) => { if (e.key === "Escape" && cur) stop(cur); });
}

