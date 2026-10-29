// App-wide state: data loaded at start (agents, models, accounts, skills) and the current agent.
import { api } from "./api.js";
import { esc } from "./ui/dom.js";

// Colour schemes of the agents (from the design). Agents store only the palette name.
export const PALETTE = {
  blau:   { "--accent": "#7fd3ff", "--accent-rgb": "127,211,255", "--on-accent": "#00344a", "--container": "#0e2a38", "--on-container": "#c4ecff", "--face-top": "#ffffff", "--face-bottom": "#dfe7f1", "--glow": "rgba(60,170,220,.35)" },
  pink:   { "--accent": "#ff8fc8", "--accent-rgb": "255,143,200", "--on-accent": "#5c0036", "--container": "#3a1029", "--on-container": "#ffd8ea", "--face-top": "#ffe9f4", "--face-bottom": "#f6bfdc", "--glow": "rgba(255,90,170,.38)" },
  mint:   { "--accent": "#7ee2b8", "--accent-rgb": "126,226,184", "--on-accent": "#003824", "--container": "#0f2e22", "--on-container": "#b8f3d6", "--face-top": "#f0fff7", "--face-bottom": "#c3ecd7", "--glow": "rgba(70,200,150,.35)" },
  gelb:   { "--accent": "#ffc85a", "--accent-rgb": "255,200,90", "--on-accent": "#3d2a00", "--container": "#3a2c0c", "--on-container": "#ffe6b0", "--face-top": "#fffaf0", "--face-bottom": "#f3dfb4", "--glow": "rgba(255,190,70,.35)" },
  lila:   { "--accent": "#b79cff", "--accent-rgb": "183,156,255", "--on-accent": "#2a1460", "--container": "#251a45", "--on-container": "#e4daff", "--face-top": "#f7f3ff", "--face-bottom": "#d9ccf6", "--glow": "rgba(150,120,255,.35)" },
  orange: { "--accent": "#ff9f7a", "--accent-rgb": "255,159,122", "--on-accent": "#4a1600", "--container": "#3d1d12", "--on-container": "#ffd9cb", "--face-top": "#fff4f0", "--face-bottom": "#f5cdbf", "--glow": "rgba(255,130,90,.35)" },
};

// Display info for the Claude models (anything unknown falls back to its id).
const MODEL_INFO = {
  haiku:  { short: "Haiku",  icon: "bolt",       desc: "Schnell und sparsam" },
  sonnet: { short: "Sonnet", icon: "balance",    desc: "Ausgewogen für fast alles" },
  opus:   { short: "Opus",   icon: "psychology", desc: "Am stärksten, braucht mehr Limit" },
  fable:  { short: "Fable",  icon: "auto_stories", desc: "Kreativ" },
};
// Antigravity models come as "agy:<model>" (e.g. agy:gemini-3-pro) and run on Antigravity accounts
const pretty = (s) => s.replace(/[-_]/g, " ").replace(/\b([a-z])/g, (c) => c.toUpperCase()).replace(/(\d) (\d)/g, "$1.$2");
export const isAgy = (id) => String(id || "").startsWith("agy:");
export function modelInfo(id) {
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

export const state = {
  boot: null,        // /bootstrap
  agent: null,       // currently selected agent
  usage: {},         // account id -> percent of the 5h window (for the model menu)
  listeners: new Set(),
};

export const colors = (a) => PALETTE[a?.palette] || PALETTE.blau;
// Profile picture of the agent, or its first letter when it has none (inside .dot / .ag-dot / .mdot).
export const face = (a) => (a?.avatar ? `<img src="${esc(a.avatar)}" alt="">` : esc(a?.name?.[0] || ""));
export const agents = () => state.boot?.agents || [];
export const mainAgent = () => agents().find((a) => a.is_main) || agents()[0];
export const agentById = (id) => agents().find((a) => a.id === id);
export const agentOf = (id) => agentById(id) || mainAgent();

export async function loadBoot() {
  state.boot = await api.get("/bootstrap");
  if (!state.agent || !agentById(state.agent.id)) state.agent = agentById(localStorage.getItem("agent")) || mainAgent();
  else state.agent = agentById(state.agent.id);
  emit();
  return state.boot;
}

export function onChange(fn) { state.listeners.add(fn); return () => state.listeners.delete(fn); }
export function emit() { state.listeners.forEach((fn) => fn(state)); }

export function setUnread(n) {
  if (state.boot) state.boot.unread = n;
  emit();
}

export async function loadUsage() {
  try {
    const list = await api.get("/usage");
    for (const a of list) {
      const w = (a.windows || []).find((x) => x.id === "five_hour");
      state.usage[a.id] = w ? Math.round(w.percent ?? 0) : null;
    }
    emit();
  } catch { /* limits are optional */ }
}
