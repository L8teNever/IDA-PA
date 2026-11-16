// Gemeinsamer Zustand: Startdaten von IDA (Modelle, Accounts, Agents), Limits, Projekte mit ihren Threads.
import { api } from "./api.js";

export const state = {
  boot: null,       // /bootstrap von IDA
  app: {},          // /app: Adressen (Link zurück zu IDA)
  usage: {},        // Account-ID -> Prozent vom 5-Std-Limit
  projects: [],     // [{ ...projekt, threadList: [...] }]
  github: null,     // /github/status
  loaded: false,
};

const listeners = new Set();
export function onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function emit() { listeners.forEach((fn) => fn(state)); }

export const agents = () => state.boot?.agents || [];
export const mainAgent = () => agents().find((a) => a.is_main) || agents()[0];
export const codeEnabled = () => !!state.boot?.code_enabled;
export const projectById = (id) => state.projects.find((p) => p.id === id);

export async function loadBoot() {
  const [boot, app] = await Promise.all([api.get("/bootstrap"), api.get("/app").catch(() => ({}))]);
  state.boot = boot;
  state.app = app || {};
  emit();
  return boot;
}

// Projekte + Threads (für die Seitenleiste) in einem Aufruf. Gibt true zurück, wenn gerade etwas läuft (dann öfter nachsehen).
// Neu gezeichnet wird nur, wenn sich wirklich etwas geändert hat – die meisten Abfragen bringen nichts Neues.
let treeSig = "";
let oldTree = false; // IDA ohne /code/tree (ältere Version): wie früher pro Projekt fragen
async function fetchTree() {
  if (!oldTree) {
    try { return await api.get("/code/tree"); } catch (e) { if (e.status !== 404) throw e; oldTree = true; }
  }
  const list = await api.get("/code/projects");
  return Promise.all(list.map(async (p) => ({
    ...p,
    threadList: p.status === "ready" || p.threads ? await api.get(`/code/projects/${p.id}/threads`).catch(() => []) : [],
  })));
}

export async function loadTree() {
  if (!codeEnabled()) { state.projects = []; state.loaded = true; treeSig = ""; emit(); return false; }
  const list = await fetchTree();
  for (const p of list) p.threadList ||= [];
  const sig = JSON.stringify(list);
  if (sig !== treeSig || !state.loaded) {
    treeSig = sig;
    state.projects = list;
    state.loaded = true;
    emit();
  }
  return list.some((p) => p.status === "cloning" || p.running || p.threadList.some((t) => t.running || t.waiting));
}

// GitHub-Anmeldung: ändert sich nur über die Einstellungen – deshalb nicht bei jeder Abfrage mitholen
export async function loadGithub() {
  const gh = await api.get("/github/status").catch(() => null);
  if (JSON.stringify(gh) !== JSON.stringify(state.github)) { state.github = gh; emit(); }
  return gh;
}

export function sessionPercent(windows) {
  const list = windows || [];
  const w = list.find((x) => x.id === "five_hour")
    || list.find((x) => !x.muted && /(-5h|-quota)$/.test(x.id || ""))
    || list.find((x) => !x.muted);
  return w && w.percent != null ? Math.round(w.percent) : null;
}

export async function loadUsage() {
  try {
    const list = await api.get("/usage");
    let changed = false;
    for (const a of list) {
      const v = sessionPercent(a.windows);
      if (state.usage[a.id] !== v) { state.usage[a.id] = v; changed = true; }
    }
    if (changed) emit();
  } catch { /* Limits sind nur eine Anzeige */ }
}

// ---- Modelle (wie in IDA) ----
const MODEL_INFO = {
  haiku: { short: "Haiku", icon: "bolt", desc: "Schnell und sparsam" },
  sonnet: { short: "Sonnet", icon: "balance", desc: "Ausgewogen für fast alles" },
  opus: { short: "Opus", icon: "psychology", desc: "Am stärksten, braucht mehr Limit" },
  fable: { short: "Fable", icon: "auto_stories", desc: "Kreativ" },
};
const pretty = (s) => s.replace(/[-_]/g, " ").replace(/\b([a-z])/g, (c) => c.toUpperCase()).replace(/(\d) (\d)/g, "$1.$2");
export const isAgy = (id) => String(id || "").startsWith("agy:");
export function modelInfo(id) {
  id = String(id || "");
  if (id === "auto") return { id, short: "Auto", icon: "auto_awesome", desc: "Wählt Anbieter, Modell und Stärke pro Nachricht selbst", name: "Auto" };
  const label = state.boot?.model_names?.[id];
  if (isAgy(id)) {
    const rest = id.slice(4);
    const short = /gemini/i.test(rest) ? "Gemini" : /claude/i.test(rest) ? "Claude" : /gpt/i.test(rest) ? "GPT" : pretty(rest.split("-")[0]);
    const icon = /gemini/i.test(rest) ? "diamond" : /claude/i.test(rest) ? "psychology" : /gpt/i.test(rest) ? "auto_awesome" : "rocket_launch";
    return { id, short, icon, desc: "über Google Antigravity", name: label || pretty(rest) };
  }
  const key = Object.keys(MODEL_INFO).find((k) => id.includes(k));
  const ver = (id.match(/(\d+)-(\d+)$/) || []).slice(1).join(".");
  const base = key ? MODEL_INFO[key] : { short: id, icon: "smart_toy", desc: "" };
  return { id, ...base, name: label ? label.replace(/^Claude /, "") : `${base.short}${ver ? " " + ver : ""}` };
}

// Rechte eines Threads
export const MODES = [
  ["ask", "front_hand", "Nachfragen", "Jede Änderung und jeder Befehl wartet auf dein OK"],
  ["edits", "edit_note", "Edits automatisch", "Dateien ändern ohne Nachfrage, Befehle fragen nach"],
  ["full", "bolt", "Voller Zugriff", "Alles ohne Nachfrage"],
];
export const modeOf = (m) => MODES.find((x) => x[0] === m) || MODES[0];

export const repoName = (url) => (url || "").replace(/^https?:\/\/[^/]+\//, "").replace(/^file:\/\/.*\//, "").replace(/\.git$/, "");
