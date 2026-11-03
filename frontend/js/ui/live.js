// Live button in the input field: a spoken conversation with Stella (Gemini Live) – no phone call needed.
// IDA hands out a one-minute ticket, the browser talks to the voice container directly:
// microphone -> PCM 16 kHz (AudioWorklet) -> WebSocket -> Gemini; Gemini's PCM 24 kHz comes back and is played in order.
import { api } from "../api.js";
import { agentById, state } from "../store.js";
import { $, esc, toast } from "./dom.js";
import { setLive } from "./mascot.js";

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

let cur = null; // the running conversation

function view(name) {
  const el = document.createElement("div");
  el.className = "live-view";
  el.innerHTML = `
    <div class="live-card" role="dialog" aria-label="Live-Gespräch mit ${esc(name)}">
      <div class="live-orb" data-state="connect"><span class="ms">graphic_eq</span></div>
      <div class="live-name">${esc(name)}</div>
      <div class="live-state">Verbinde …</div>
      <div class="live-lines" aria-live="polite"></div>
      <div class="live-actions">
        <button type="button" class="live-mute" aria-label="Stummschalten"><span class="ms">mic</span></button>
        <button type="button" class="live-end" aria-label="Auflegen"><span class="ms">call_end</span></button>
      </div>
    </div>`;
  document.body.appendChild(el);
  return el;
}

function setState(c, st, text) {
  c.el.querySelector(".live-orb").dataset.state = st;
  c.el.querySelector(".live-state").textContent = text;
}

// Transcript: consecutive pieces of the same speaker go into one line; only the last few lines stay visible.
function addText(c, who, piece) {
  const box = c.el.querySelector(".live-lines");
  let last = box.lastElementChild;
  if (!last || last.dataset.who !== who) {
    last = document.createElement("p");
    last.dataset.who = who;
    box.appendChild(last);
    while (box.children.length > 6) box.firstElementChild.remove();
  }
  last.textContent += piece;
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
  src.connect(c.ctx.destination);
  const at = Math.max(c.ctx.currentTime + 0.02, c.next);
  src.start(at);
  c.next = at + ab.duration;
  c.sources.add(src);
  src.onended = () => {
    c.sources.delete(src);
    if (!c.sources.size && !c.closing) setState(c, "listen", "Hört zu");
  };
  setState(c, "speak", `${c.name} spricht`);
}

function flush(c) { // the user interrupted: drop what Stella had still queued
  for (const s of c.sources) { try { s.stop(); } catch { /* already stopped */ } }
  c.sources.clear();
  c.next = 0;
}

async function stop(c, note) {
  if (!c || c.closed) return;
  c.closed = true;
  try { c.ws?.readyState === 1 && c.ws.send(JSON.stringify({ type: "end" })); } catch { /* closed */ }
  try { c.ws?.close(); } catch { /* closed */ }
  c.stream?.getTracks().forEach((t) => t.stop());
  try { await c.ctx?.close(); } catch { /* closed */ }
  c.el.classList.add("out");
  setTimeout(() => c.el.remove(), 250);
  document.body.classList.remove("live-on");
  setLive(null);
  if (cur === c) cur = null;
  if (note) toast(note);
}

async function start() {
  if (cur) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) return toast("Dieser Browser kann kein Live-Gespräch");
  const agent = agentById(state.boot?.voice_agent) || { name: "Stella" };
  const c = { name: agent.name, sources: new Set(), next: 0, rate: 24000, closed: false, closing: false };
  cur = c;
  c.el = view(agent.name);
  document.body.classList.add("live-on");
  setLive("phone");
  c.el.querySelector(".live-end").onclick = () => stop(c);
  c.el.querySelector(".live-mute").onclick = (e) => {
    c.muted = !c.muted;
    c.node?.port.postMessage({ muted: c.muted });
    e.currentTarget.classList.toggle("on", c.muted);
    e.currentTarget.querySelector(".ms").textContent = c.muted ? "mic_off" : "mic";
  };
  try {
    // the AudioContext has to be made right in the tap (iPhone) – before anything is awaited
    c.ctx = new (window.AudioContext || window.webkitAudioContext)();
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
        const src = c.ctx.createMediaStreamSource(stream);
        c.node = new AudioWorkletNode(c.ctx, "down16k");
        c.node.port.onmessage = (ev) => c.ws.readyState === 1 && c.ws.send(ev.data);
        src.connect(c.node);
        setState(c, "listen", m.offline ? "Hört zu (ohne Zugriff auf deine Daten)" : "Hört zu");
      } else if (m.type === "text") addText(c, m.who, m.text);
      else if (m.type === "interrupted") { flush(c); setState(c, "listen", "Hört zu"); }
      else if (m.type === "tool") setState(c, m.running ? "tool" : "listen", m.running ? "Schaut nach …" : "Hört zu");
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

export function initLive() {
  const b = $("liveBtn");
  if (!b) return;
  b.hidden = !state.boot?.voice;
  b.onclick = (e) => { e.preventDefault(); start(); };
  addEventListener("keydown", (e) => { if (e.key === "Escape" && cur) stop(cur); });
}
