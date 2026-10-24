// The mascot: moods (animations), eyes follow the pointer, sleeps at night.
import { $, openSheet, closeSheet } from "./dom.js";

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

let mood = "idle";
let timer = null;

export function baseMood() {
  const h = new Date().getHours();
  return h >= 22 || h < 6 ? "sleep" : "idle";
}

// setMood("phone") – optionally only for ms milliseconds
export function setMood(id, ms) {
  clearTimeout(timer);
  mood = MOODS.some((m) => m.id === id) ? id : "idle";
  document.body.dataset.mood = mood;
  const m = MOODS.find((x) => x.id === mood);
  document.querySelectorAll(".buddy .props").forEach((p) => (p.innerHTML = m.props || ""));
  document.querySelectorAll(".buddy .eyes").forEach((e) => (e.style.transform = ""));
  if (ms) timer = setTimeout(() => setMood(baseMood()), ms);
}

// Mood that fits a tool the agent is calling.
export function moodForTool(name = "") {
  const n = name.toLowerCase();
  if (/mail/.test(n)) return "mail";
  if (/termin|calendar|event|kalender/.test(n)) return "calendar";
  if (/call|anruf|phone|telefon|stella/.test(n)) return "phone";
  if (/search|such|find|list/.test(n)) return "search";
  if (/memory/.test(n)) return "idea";
  return "thinking";
}

function openPicker() {
  document.body.classList.add("mood-sheet");
  openSheet(`
    <div class="sub-head" style="padding-top:0"><h1 style="font-size:22px">Animationen</h1><button class="icon-btn es-x" id="moodClose" aria-label="Schließen"><span class="ms">close</span></button></div>
    <p class="sub-intro" style="margin:-6px 0 14px">Tipp eine an, um sie zu sehen. Sie laufen auch automatisch, z. B. wenn ein Agent Mails schreibt oder sucht.</p>
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
  setMood(baseMood());
  ["buddySmall", "buddyBig"].forEach((id) => $(id).addEventListener("click", openPicker));
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
  setInterval(() => { if (mood === "idle" || mood === "sleep") setMood(baseMood()); }, 600000);
}
