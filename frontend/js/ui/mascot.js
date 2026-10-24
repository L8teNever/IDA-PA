// The mascot: moods (animations) that follow what the agent is doing, eyes follow the pointer, sleeps when idle.
import { $, openSheet, closeSheet } from "./dom.js";
import { api } from "../api.js";
import { state } from "../store.js";

export const MOODS = [
  { id: "idle", name: "Normal", icon: "sentiment_neutral" },
  { id: "happy", name: "Glücklich", icon: "sentiment_very_satisfied", props: `<i class="blush l"></i><i class="blush r"></i><span class="ms p-spark s1">auto_awesome</span><span class="ms p-spark s2">auto_awesome</span>` },
  { id: "love", name: "Verliebt", icon: "favorite", props: `<i class="blush l"></i><i class="blush r"></i><span class="ms p-heart h1">favorite</span><span class="ms p-heart h2">favorite</span><span class="ms p-heart h3">favorite</span>` },
  { id: "sleep", name: "Schläft", icon: "bedtime", props: `<b class="z z1">z</b><b class="z z2">z</b><b class="z z3">Z</b>` },
  { id: "phone", name: "Telefoniert", icon: "call", props: `<span class="ms p-phone">call</span><i class="wave w1"></i><i class="wave w2"></i>` },
  { id: "calendar", name: "Trägt Termin ein", icon: "edit_calendar", props: `<span class="ms p-cal">calendar_month</span><span class="ms p-pen">edit</span>` },
  { id: "mail", name: "Schreibt Mail", icon: "mail", props: `<span class="ms p-mail m1">mail</span><span class="ms p-mail m2">mail</span>` },
  { id: "search", name: "Sucht", icon: "search", props: `<span class="ms p-search">search</span>` },
  { id: "thinking", name: "Denkt nach", icon: "psychology", props: `<span class="p-think"><i></i><i></i><i></i></span>` },
  { id: "idea", name: "Hat eine Idee", icon: "lightbulb", props: `<span class="ms p-bulb">lightbulb</span>` },
  { id: "listen", name: "Hört zu", icon: "hearing", props: `<span class="ms p-ear l">graphic_eq</span><span class="ms p-ear r">graphic_eq</span>` },
  { id: "sad", name: "Traurig", icon: "sentiment_dissatisfied", props: `<i class="p-drop"></i>` },
];

// The mood follows what the shown agent is really doing. Priority:
// picked by hand > short reaction (happy/sad) > live chat in this tab > server activity > user typing > awake/asleep.
const SLEEP_AFTER = 60000; // nothing going on for a minute -> falls asleep
const POLL_MS = 2500;
let mood = "";
let manual = null, manualUntil = 0;
let flash = null, flashUntil = 0;
let live = null;
let typedAt = 0;
let lastBusy = Date.now();
let activity = { agents: {}, done: {} };
let doneSeen = null;

const night = () => { const h = new Date().getHours(); return h >= 22 || h < 6; };
const shownAgent = () => state.agent?.id;

export function baseMood() {
  return night() || Date.now() - lastBusy > SLEEP_AFTER ? "sleep" : "idle";
}

function wanted() {
  const now = Date.now();
  if (manual && now < manualUntil) return manual;
  if (flash && now < flashUntil) return flash;
  if (live) return live;
  const a = activity.agents[shownAgent()];
  if (a) return a.state === "tool" ? moodForTool(a.tool) : "thinking";
  if (now - typedAt < 5000) return "listen";
  return baseMood();
}

function render(id) {
  if (id === mood) return;
  mood = id;
  document.body.dataset.mood = mood;
  const m = MOODS.find((x) => x.id === mood);
  document.querySelectorAll(".buddy .props").forEach((p) => (p.innerHTML = m.props || ""));
  document.querySelectorAll(".buddy .eyes").forEach((e) => (e.style.transform = ""));
}

function update() {
  const id = wanted();
  if (id !== "idle" && id !== "sleep") lastBusy = Date.now();
  render(id);
}

// Show a mood by hand (animation picker): stays for a while, then the automatic mood takes over again.
export function setMood(id, ms = 20000) {
  manual = MOODS.some((m) => m.id === id) ? id : "idle";
  manualUntil = Date.now() + ms;
  update();
}

// Live mood from a chat streaming in this tab (null = over).
export function setLive(id) {
  live = id;
  if (id) manual = null;
  update();
}

// Short reaction, e.g. happy when a run went well.
export function flashMood(id, ms = 2500) {
  flash = id;
  flashUntil = Date.now() + ms;
  update();
}

async function poll() {
  if (document.hidden) return;
  try {
    activity = await api.get("/activity");
  } catch { return; }
  const done = activity.done[shownAgent()];
  const key = `${shownAgent()}:${done?.seq ?? ""}`;
  if (doneSeen !== null && key !== doneSeen && done && done.ago < 15 && !live) flashMood(done.ok ? "happy" : "sad");
  doneSeen = key;
  update();
}

// Mood that fits a tool the agent is calling.
export function moodForTool(name = "") {
  const n = (name || "").toLowerCase();
  if (/call|anruf|phone|telefon|stella/.test(n)) return "phone";
  if (/agent_delegate|agent_send/.test(n)) return "listen"; // waits for a colleague
  if (/mail|nachricht|message|telegram|_send|senden/.test(n)) return "mail";
  if (/termin|calendar|event|kalender|erinnerung|routine_create|routine_update|aufgabe/.test(n)) return "calendar";
  if (/memory_save|memory_update|notify|notiz/.test(n)) return "idea";
  if (/search|such|find|list|lesen|read|get|stundenplan|vertretung/.test(n)) return "search";
  return "thinking";
}

function openPicker() {
  document.body.classList.add("mood-sheet");
  openSheet(`
    <div class="sub-head" style="padding-top:0"><h1 style="font-size:22px">Animationen</h1><button class="icon-btn es-x" id="moodClose" aria-label="Schließen"><span class="ms">close</span></button></div>
    <p class="sub-intro" style="margin:-6px 0 14px">Tipp eine an, um sie kurz zu sehen. Sonst wechselt die Stimmung von selbst: schläft, wenn nichts läuft, telefoniert, schreibt Mails, sucht, hört dir beim Tippen zu …</p>
    <div class="mood-grid" id="moodGrid">${MOODS.map((m) => `<button data-mood="${m.id}" class="${m.id === mood ? "on" : ""}"><span class="ms">${m.icon}</span>${m.name}</button>`).join("")}</div>`);
  $("moodClose").onclick = closeSheet;
  $("moodGrid").onclick = (e) => {
    const b = e.target.closest("[data-mood]");
    if (!b) return;
    setMood(b.dataset.mood);
    $("moodGrid").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  };
}

export function initMascot() {
  update();
  ["buddySmall", "buddyBig"].forEach((id) => $(id).addEventListener("click", openPicker));
  $("input")?.addEventListener("input", () => { typedAt = Date.now(); update(); });
  addEventListener("pointermove", (e) => {
    if (mood !== "idle") return;
    const big = !$("agentHome").hidden;
    const el = $(big ? "eyesBig" : "eyes"), k = big ? 2.2 : 1;
    const r = el.getBoundingClientRect();
    if (!r.width) return;
    const dx = (e.clientX - (r.left + r.width / 2)) / innerWidth;
    const dy = (e.clientY - (r.top + r.height / 2)) / innerHeight;
    el.style.transform = `translate(calc(-50% + ${dx * 10 * k}px), calc(-50% + ${dy * 7 * k}px))`;
  });
  setInterval(update, 1000); // timeouts (reaction over, falls asleep), agent switched
  setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  poll();
}
