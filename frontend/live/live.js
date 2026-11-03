// Stand-alone live app "IDA Live" (PWA on the voice host): opens straight into the conversation with Stella.
// The key comes once through the install link from IDA (https://<voice host>/#key=…) and stays in localStorage.
// With it the app gets a one-minute ticket from this host and talks to Gemini Live over /live (same protocol as IDA's /live page).
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

const PINK = { "--accent": "#ff8fc8", "--accent-rgb": "255,143,200", "--on-accent": "#5c0036", "--container": "#3a1029", "--on-container": "#ffd8ea", "--face-top": "#ffe9f4", "--face-bottom": "#f6bfdc", "--glow": "rgba(255,90,170,.38)" };
for (const [k, v] of Object.entries(PINK)) document.documentElement.style.setProperty(k, v);

const STATES = { connect: "Verbinde …", listen: "Hört zu", speak: "spricht", tool: "Schaut nach …", muted: "Stumm" };
const $ = (id) => document.getElementById(id);
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const rms = (a) => { let s = 0; for (let i = 0; i < a.length; i += 4) s += a[i] * a[i]; return Math.min(1, Math.sqrt(s / Math.max(1, a.length / 4)) / 6000); };

// key from the install link
if (location.hash.startsWith("#key=")) {
  localStorage.setItem("liveKey", decodeURIComponent(location.hash.slice(5)));
  history.replaceState(null, "", location.pathname);
}
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

let gestureCtx = null;
let cur = null;

const buddy = `<div class="buddy" aria-hidden="true"><div class="face"><div class="eyes"><div class="eye"></div><div class="eye"></div></div></div><div class="props"></div></div>`;

function shell(inner) {
  $("page").innerHTML = `<div class="live live-b">
    <div class="lv-buddy-wrap" id="lvBuddyWrap" data-state="idle">${buddy}</div>${inner}</div>`;
  const el = $("page").querySelector(".live-b");
  el.addEventListener("touchmove", (e) => { if (!e.target.closest(".lv-chat")) e.preventDefault(); }, { passive: false });
}

function home(problem, ended) {
  const key = localStorage.getItem("liveKey");
  shell(`<div class="lv-status"><span class="lv-pill ${problem && !ended ? "fail" : ""}"><span class="ms">${ended ? "call_end" : problem ? "error" : "graphic_eq"}</span>${esc(ended ? "Gespräch beendet" : problem || (key ? "bereit" : "Nicht eingerichtet"))}</span></div>
    <div class="lv-chat" id="lvChat">${key ? "" : `<div class="lv-msg bot"><small>IDA Live</small>Öffne diese App einmal über den Link aus IDA (Einstellungen → Stella-Stimme → Live-App), dann ist sie eingerichtet.</div>`}</div>
    <div class="lv-btns">${key ? `<button class="es-btn primary" id="lvGo"><span class="ms">graphic_eq</span>Gespräch starten</button>` : ""}</div>`);
  if (key) $("lvGo").onclick = () => { prime(); start(); };
}

function prime() {
  try { gestureCtx = new (window.AudioContext || window.webkitAudioContext)(); gestureCtx.resume?.(); } catch { gestureCtx = null; }
}

function draw(c) {
  const st = c.muted && c.state === "listen" ? "muted" : c.state;
  $("lvBuddyWrap").dataset.state = st;
  $("lvPill").className = `lv-pill ${st}`;
  $("lvState").textContent = st === "speak" ? `${c.name} spricht` : STATES[st] || "";
  $("lvIcon").textContent = { speak: "record_voice_over", tool: "travel_explore", muted: "mic_off" }[st] || "graphic_eq";
}
const setState = (c, st) => { c.state = st; draw(c); };

function addLine(c, who, piece) {
  const box = $("lvChat");
  if (!box) return;
  let last = box.lastElementChild;
  if (!last || last.dataset.who !== who) {
    last = document.createElement("div");
    last.className = `lv-msg ${who === "user" ? "user" : "bot"}`;
    last.dataset.who = who;
    last.innerHTML = `<small>${who === "user" ? "Du" : esc(c.name)}</small><span class="lv-text"></span>`;
    box.appendChild(last);
  }
  last.querySelector(".lv-text").textContent += piece;
  const log = c.lines;
  if (log.length && log[log.length - 1].who === who) log[log.length - 1].text += piece; else log.push({ who, text: piece });
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
  c.outLevel = Math.max(c.outLevel, rms(pcm));
  src.onended = () => { c.sources.delete(src); if (!c.sources.size && !c.closing && c.state === "speak") setState(c, "listen"); };
  if (c.state !== "speak") setState(c, "speak");
}

function flush(c) {
  for (const s of c.sources) { try { s.stop(); } catch { /* already stopped */ } }
  c.sources.clear();
  c.next = 0;
}

function tick(c) {
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

async function start() {
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) return home("Dieser Browser kann kein Live-Gespräch.");
  const c = { name: "Stella", sources: new Set(), next: 0, rate: 24000, closed: false, closing: false, state: "connect",
              inLevel: 0, outLevel: 0, shown: 0, t0: 0, muted: false, ctx: gestureCtx, lines: [] };
  cur = c;
  gestureCtx = null;
  shell(`<div class="lv-status"><span class="lv-pill" id="lvPill"><span class="ms" id="lvIcon">graphic_eq</span><span id="lvState"></span></span><span class="lv-time" id="lvTime">0:00</span></div>
    <div class="lv-tool" id="lvTool" hidden><span class="ms">travel_explore</span><span id="lvToolText"></span></div>
    <div class="lv-chat" id="lvChat" aria-live="polite"></div>
    <div class="lv-btns"><button class="es-btn" id="lvMute"><span class="ms">mic</span>Stumm</button>
      <button class="es-btn" id="lvPhone"><span class="ms">phone_forwarded</span>Am Telefon weiter</button>
      <button class="es-btn lv-end" id="lvEnd"><span class="ms">call_end</span>Auflegen</button></div>`);
  const root = $("page").querySelector(".live-b");   // intro: Stella wacht groß in der Mitte auf, danach wandert sie nach oben
  root.classList.add("intro");
  const props = root.querySelector(".buddy .props");   // erst schläft sie – exakt wie in IDA (Stimmung „sleep“ mit Zzz) –, dann wacht sie auf
  document.body.dataset.mood = "sleep";
  if (props) props.innerHTML = `<b class="z z1">z</b><b class="z z2">z</b><b class="z z3">Z</b>`;
  setTimeout(() => {
    delete document.body.dataset.mood;
    if (props) props.innerHTML = "";
    root.classList.add("waking");
    setTimeout(() => root.classList.remove("waking"), 800);
  }, 3000);
  c.introEnd = Date.now() + 4000;
  c.endIntro = () => { const left = Math.max(0, c.introEnd - Date.now()); setTimeout(() => root.classList.remove("intro"), left); };
  $("lvEnd").onclick = () => stop(c);
  $("lvPhone").onclick = async () => {  // only on tap – never switches by itself
    const b = $("lvPhone");
    b.disabled = true;
    try {
      const r = await fetch("/pwa/phone", { method: "POST", headers: { "x-live-key": localStorage.getItem("liveKey") || "", "content-type": "application/json" },
                                            body: JSON.stringify({ lines: c.lines }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Anruf geht gerade nicht");
      stop(c, "", "Stella ruft dich gleich an – bitte ans Telefon gehen");
    } catch (e) { b.disabled = false; addLine(c, "bot", `\n(${e.message})`); }
  };
  $("lvMute").onclick = () => {
    c.muted = !c.muted;
    c.node?.port.postMessage({ muted: c.muted });
    $("lvMute").innerHTML = c.muted ? `<span class="ms">mic_off</span>Ton an` : `<span class="ms">mic</span>Stumm`;
    $("lvMute").classList.toggle("primary", c.muted);
    draw(c);
  };
  draw(c);
  c.clock = setInterval(() => tick(c), 100);
  c.onVisible = () => { if (document.visibilityState === "visible") wake(c); };
  document.addEventListener("visibilitychange", c.onVisible);
  wake(c);
  try {
    const [stream, t] = await Promise.all([
      navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } }),
      fetch("/pwa/ticket", { method: "POST", headers: { "x-live-key": localStorage.getItem("liveKey") || "" } }).then(async (r) => {
        if (r.status === 403) { localStorage.removeItem("liveKey"); throw new Error("Schlüssel ungültig – bitte den Link aus IDA nochmal öffnen"); }
        if (!r.ok) throw new Error("Live-Gespräch geht gerade nicht");
        return r.json();
      }),
    ]);
    c.stream = stream;
    c.name = t.name || c.name;
    if (c.closed) return stream.getTracks().forEach((x) => x.stop());
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
    await c.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    c.ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/live?ticket=${t.ticket}`);
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
        c.endIntro();
      } else if (m.type === "text") addLine(c, m.who, m.text);
      else if (m.type === "interrupted") { flush(c); setState(c, "listen"); }
      else if (m.type === "tool") {
        setState(c, m.running ? "tool" : "listen");
        $("lvTool").hidden = !m.running;
        if (m.running) $("lvToolText").textContent = m.name ? `schaut nach (${m.name}) …` : "schaut nach …";
      } else if (m.type === "error") stop(c, m.message || "Das Gespräch ist abgebrochen");
      else if (m.type === "end") {
        c.closing = true;
        setTimeout(() => stop(c), Math.max(0, (c.next - c.ctx.currentTime) * 1000) + 300);
      }
    };
    c.ws.onclose = () => { if (!c.closing) stop(c, c.closed ? "" : "Verbindung zu Stella getrennt"); };
  } catch (e) {
    stop(c, e?.name === "NotAllowedError" ? "Kein Zugriff aufs Mikrofon – bitte im Browser erlauben" : e?.message || "Live-Gespräch geht gerade nicht");
  }
}

async function stop(c, problem, note) {
  if (!c || c.closed) return;
  c.closed = true;
  try { c.ws?.readyState === 1 && c.ws.send(JSON.stringify({ type: "end" })); } catch { /* closed */ }
  try { c.ws?.close(); } catch { /* closed */ }
  c.stream?.getTracks().forEach((t) => t.stop());
  clearInterval(c.clock);
  document.removeEventListener("visibilitychange", c.onVisible);
  c.lock?.release().catch(() => {});
  try { await c.ctx?.close(); } catch { /* closed */ }
  if (cur === c) cur = null;
  home(problem || "", !problem);
  if (note) { const p = $("lvChat"); if (p) p.innerHTML = `<div class="lv-msg bot"><small>IDA Live</small>${esc(note)}</div>`; }
}

// Opened as the installed app: the talk starts by itself (Chrome lets installed apps play sound without a tap;
// if the browser still blocks the sound, the start button stays).
async function boot() {
  const installed = matchMedia("(display-mode: standalone)").matches || matchMedia("(display-mode: fullscreen)").matches || navigator.standalone;
  if (!installed || !localStorage.getItem("liveKey")) return home();
  prime();
  if (gestureCtx) await Promise.race([gestureCtx.resume().catch(() => {}), new Promise((r) => setTimeout(r, 600))]);
  if (gestureCtx?.state === "running") start();
  else { gestureCtx?.close().catch(() => {}); gestureCtx = null; home(); }
}
boot();
