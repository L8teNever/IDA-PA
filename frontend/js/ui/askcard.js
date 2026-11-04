// When an agent asks another one (agent_delegate) or writes to it (agent_send), the chat shows a card instead of a tool
// line: who talks to whom, the question, how far it is, and the answer as soon as it is there.
import { api } from "../api.js";
import { agentById, agents, colors, face } from "../store.js";
import { esc, md } from "./dom.js";

const POLL_MS = 4000;
const tasks = new Map(); // message id → { status, answer }
const FINAL = new Set(["done", "silent"]);
let timer = null;

export const isAsk = (p) => /(^|_)agent_(delegate|send)$/.test(p.name || "");
const isTask = (p) => /delegate$/.test(p.name);

const parsed = (p) => { try { return JSON.parse(p.out); } catch { return {}; } };
function target(p) {
  const to = String(parsed(p).an || p.input?.to || "").toLowerCase();
  return agentById(to) || agents().find((a) => a.name.toLowerCase() === to) || null;
}
const dot = (a) => {
  const c = colors(a);
  return `<span class="ask-dot" style="background:${c["--accent"]};color:${c["--on-accent"]}">${a ? face(a) : `<span class="ms">person</span>`}</span>`;
};

function liveHtml(name, id) {
  const t = tasks.get(id);
  if (t?.status === "done") return `<div class="ask-state ok"><span class="ms">check_circle</span>${esc(name)} hat geantwortet</div><div class="ask-a">${md(t.answer || "")}</div>`;
  if (t?.status === "silent") return `<div class="ask-state"><span class="ms">schedule</span>Keine Antwort gekommen</div>`;
  return `<div class="ask-state"><span class="ms spin">progress_activity</span>${t?.status === "working" ? `${esc(name)} arbeitet daran …` : `Wartet auf ${esc(name)} …`}</div>`;
}

export function askHtml(p, fromId) {
  const from = agentById(fromId), to = target(p), task = isTask(p);
  const name = to?.name || p.input?.to || "Agent";
  const text = p.input?.task || p.input?.text || "";
  const id = !p.error && p.out !== undefined ? parsed(p).id : null;
  const live = p.error ? `<div class="ask-state"><span class="ms">error</span>${esc(p.out || "Hat nicht geklappt")}</div>`
    : p.out === undefined ? `<div class="ask-state"><span class="ms spin">progress_activity</span>Wird geschickt …</div>`
    : task && id ? `<div class="ask-live">${liveHtml(name, id)}</div>`
    : `<div class="ask-state ok"><span class="ms">check_circle</span>Zugestellt</div>`;
  return `<div class="ask ${p.error ? "err" : ""}"${p.error ? "" : ` style="--ask:${colors(to)["--accent"]}"`}${task && id ? ` data-task="${esc(id)}" data-name="${esc(name)}"` : ""}>
    <div class="ask-head">${dot(from)}<span class="ms">arrow_forward</span>${dot(to)}<b>${esc(from?.name || "Agent")} ${task ? "fragt" : "schreibt"} ${esc(!to && parsed(p).an === "user" ? "dir" : name)}</b></div>
    ${text ? `<div class="ask-q">${esc(text)}</div>` : ""}${live}</div>`;
}

async function poll() {
  timer = null;
  const open = () => [...new Set([...document.querySelectorAll(".ask[data-task]")].map((x) => x.dataset.task))].filter((id) => !FINAL.has(tasks.get(id)?.status));
  const ids = open();
  if (!ids.length) return;
  try {
    for (const t of await api.get(`/messages/tasks?ids=${ids.slice(0, 50).map(encodeURIComponent).join(",")}`)) {
      const old = tasks.get(t.id)?.status;
      tasks.set(t.id, t);
      if (old === t.status) continue;
      document.querySelectorAll(`.ask[data-task="${CSS.escape(t.id)}"] .ask-live`).forEach((x) => { x.innerHTML = liveHtml(x.closest(".ask").dataset.name, t.id); });
    }
  } catch { /* the next round tries again */ }
  if (open().length) timer = setTimeout(poll, POLL_MS);
}

// after drawing a message: looks up the cards that still wait
export function watchAsks() {
  if (!timer && document.querySelector(".ask[data-task]")) timer = setTimeout(poll, 300);
}

// long question or answer: a tap shows all of it
document.addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  e.target.closest(".ask-q, .ask-a")?.classList.toggle("open");
});
