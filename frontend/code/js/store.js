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

// Projekte + Threads (für die Seitenleiste). Gibt true zurück, wenn gerade etwas läuft (dann öfter nachsehen).
export async function loadTree() {
  if (!codeEnabled()) { state.projects = []; state.loaded = true; emit(); return false; }
  const [list, gh] = await Promise.all([api.get("/code/projects"), api.get("/github/status").catch(() => null)]);
  const withThreads = await Promise.all(list.map(async (p) => ({
    ...p,
    threadList: p.status === "ready" || p.threads ? await api.get(`/code/projects/${p.id}/threads`).catch(() => []) : [],
  })));
  state.projects = withThreads;
  state.github = gh;
  state.loaded = true;
  emit();
  return withThreads.some((p) => p.status === "cloning" || p.running || p.threadList.some((t) => t.running || t.waiting));
}

export async function loadUsage() {
  try {
    const list = await api.get("/usage");
    for (const a of list) {
      const w = (a.windows || []).find((x) => x.id === "five_hour");
      state.usage[a.id] = w ? Math.round(w.percent ?? 0) : null;
    }
    emit();
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
  const label = state.boot?.model_names?.[id];
  if (isAgy(id)) {
    const rest = id.slice(4);
    const short = /gemini/i.test(rest) ? "Gemini" : /claude/i.test(rest) ? "Claude" : /gpt/i.test(rest) ? "GPT" : pretty(rest.split("-")[0]);
    return { id, short, icon: /gemini/i.test(rest) ? "diamond" : "rocket_launch", desc: "über Google Antigravity", name: label || pretty(rest) };
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
