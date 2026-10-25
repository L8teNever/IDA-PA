// Microphone button in the input field: record, let the server turn it into text (local Whisper),
// put the text into the field – the user can edit it and sends it themselves.
import { api } from "../api.js";
import { $, toast } from "./dom.js";
import { setLive } from "./mascot.js";

const MAX_MS = 5 * 60 * 1000;
let rec = null, chunks = [], stream = null, started = 0, timer = null, cancelled = false;

const mime = () => ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find((m) => window.MediaRecorder?.isTypeSupported?.(m)) || "";
const fmt = (ms) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

function setState(state) {
  const b = $("micBtn");
  b.dataset.state = state;
  document.body.classList.toggle("mic-on", state !== "idle");
  b.querySelector(".ms").textContent = state === "rec" ? "stop" : state === "busy" ? "progress_activity" : "mic";
  b.setAttribute("aria-label", state === "rec" ? "Aufnahme beenden" : state === "busy" ? "Wird umgewandelt …" : "Sprechen");
  $("micInfo").hidden = state === "idle";
  if (state === "busy") $("micInfo").textContent = "wird umgewandelt …";
}

async function start() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return toast("Dieser Browser kann keine Aufnahmen machen");
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch {
    return toast("Kein Zugriff aufs Mikrofon – bitte im Browser erlauben");
  }
  const type = mime();
  rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  chunks = []; cancelled = false; started = Date.now();
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.onstop = finish;
  rec.start(1000);
  setState("rec");
  setLive("listen");
  const tick = () => {
    const ms = Date.now() - started;
    $("micInfo").textContent = `● ${fmt(ms)} · nochmal tippen zum Beenden`;
    if (ms >= MAX_MS) stop();
  };
  tick();
  timer = setInterval(tick, 500);
}

function stop(cancel = false) {
  cancelled = cancel;
  clearInterval(timer);
  if (rec?.state === "recording") rec.stop();
  stream?.getTracks().forEach((t) => t.stop());
}

async function finish() {
  const type = rec?.mimeType || "audio/webm";
  rec = null;
  if (cancelled || !chunks.length || Date.now() - started < 400) { setState("idle"); setLive(null); if (cancelled) toast("Aufnahme verworfen"); return; }
  setState("busy");
  const ext = type.includes("mp4") ? "mp4" : type.includes("ogg") ? "ogg" : "webm";
  const fd = new FormData();
  fd.append("audio", new Blob(chunks, { type }), `aufnahme.${ext}`);
  try {
    const r = await api.post("/transcribe", fd);
    if (r.text) insert(r.text);
    else toast("Nichts verstanden – nochmal versuchen?");
  } catch (e) {
    toast(e.message || "Umwandeln hat nicht geklappt");
  } finally {
    setState("idle");
    setLive(null);
  }
}

function insert(text) {
  const input = $("input");
  const before = input.value.slice(0, input.selectionStart ?? input.value.length);
  const after = input.value.slice(input.selectionEnd ?? input.value.length);
  const pre = before && !/\s$/.test(before) ? " " : "";
  input.value = before + pre + text + (after && !/^\s/.test(after) ? " " : "") + after;
  input.dispatchEvent(new Event("input", { bubbles: true })); // grows the field, enables "send"
  input.focus();
  const pos = (before + pre + text).length;
  input.setSelectionRange(pos, pos);
}

export function initVoice() {
  const b = $("micBtn");
  if (!b) return;
  setState("idle");
  b.onclick = (e) => {
    e.preventDefault();
    const st = b.dataset.state;
    if (st === "idle") start();
    else if (st === "rec") stop();
  };
  addEventListener("keydown", (e) => { if (e.key === "Escape" && b.dataset.state === "rec") stop(true); });
}
