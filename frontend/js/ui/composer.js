// Input at the bottom: auto-growing text field, model/account menu with limits, "/" skill menu, send.
import { ctx, send, addSys, stopAnswer } from "../pages/chat.js";
import { enqueue, initQueue } from "./queue.js";
import { loadUsage, modelInfo, onChange, state } from "../store.js";
import { $, esc } from "./dom.js";

const ACC_COLORS = [["#282a2f", "#c4c6cf"], ["#3a2c0c", "#ffc85a"], ["#1b2a4a", "#8ab4ff"], ["#0f2e22", "#7ee2b8"], ["#3a1029", "#ff8fc8"]];
let sel = JSON.parse(localStorage.getItem("modelSel") || "null"); // { model, account }
let slashItems = [], slashIdx = 0;

const accounts = () => state.boot?.accounts || [];
const accColor = (id) => ACC_COLORS[Math.max(0, accounts().findIndex((a) => a.id === id)) % ACC_COLORS.length];
const accTag = (a) => { const [bg, fg] = accColor(a.id); return `<span class="acc-tag" style="--acc-bg:${bg};--acc-fg:${fg}">${esc(a.id.replace(/^a/, ""))}</span>`; };
const limColor = (p) => (p >= 90 ? "#ff8f8f" : p >= 75 ? "#ffb86b" : "var(--accent)");
const ring = (p, size) => (p == null ? "" : `<span class="lim-ring ${size}" style="--p:${p};--c:${limColor(p)}" title="${p} % vom 5-Std-Limit"></span>`);

function current() {
  const models = state.boot?.models || [];
  const accs = accounts();
  const model = models.includes(sel?.model) ? sel.model : state.agent?.model || state.boot?.default_model;
  // the account must be able to run the model: agy:… only on Antigravity accounts, Claude models only on Claude accounts
  const fits = (a) => a && (a.provider === "antigravity") === String(model || "").startsWith("agy:");
  const wanted = accs.find((a) => a.id === sel?.account) || accs.find((a) => a.id === state.agent?.account_id);
  const account = fits(wanted) ? wanted : accs.find(fits) || wanted || accs[0];
  const effort = effortsOf(model || "").includes(sel?.effort) && !String(model).startsWith("agy:") ? sel.effort : null;
  return { model, account, effort };
}

function renderChip() {
  const { model, account, effort } = current();
  if (!model) return;
  const m = modelInfo(model);
  $("modelIcon").textContent = m.icon;
  $("modelName").textContent = m.short + (effort ? ` · ${EFFORTS[effort]}` : "");
  $("modelBtn").querySelectorAll(".acc-tag").forEach((x) => x.remove());
  if (account && accounts().length > 1) $("modelName").insertAdjacentHTML("afterend", accTag(account));
  const p = account ? state.usage[account.id] : null;
  $("limNow").innerHTML = p == null ? "" : `${p} %${ring(p, "small")}`;
}

let menuTab = null;
// "Gemini 3.8 Flash (High)" -> base "Gemini 3.8 Flash" + thinking level; variants of one model become one row
const LEVELS = { low: "Niedrig", medium: "Mittel", high: "Hoch" };
const levelOf = (name) => { const m = /^(.*) \((low|medium|high)\)$/i.exec(name); return m ? [m[1], m[2].toLowerCase()] : [name, null]; };
const shortName = (n) => String(n || "").split("@")[0];
// Claude: thinking depth via --effort. Which levels a model knows, and what it uses without a choice
const EFFORTS = { low: "Niedrig", medium: "Mittel", high: "Hoch", xhigh: "Extra", max: "Max" };
function effortsOf(id) {
  if (/haiku-4-5|sonnet-4-5|-3-|-4-1|-4-0|-4$/.test(id)) return [];
  if (/opus-4-5/.test(id)) return ["low", "medium", "high"];
  if (/-4-6/.test(id)) return ["low", "medium", "high", "max"];
  return Object.keys(EFFORTS);
}
const effortDefault = (id) => (/opus-5-5|haiku-5-5/.test(id) ? "medium" : "high");
function renderMenu() {
  const accs = accounts();
  const { model, account, effort } = current();
  if (!menuTab || !accs.some((a) => a.id === menuTab)) menuTab = account?.id || accs[0]?.id;
  const tabs = accs.map((a) => {
    const p = state.usage[a.id], agy = a.provider === "antigravity";
    return `<button type="button" data-tab="${a.id}" class="mm-acc ${a.id === menuTab ? "on" : ""}" title="${esc(a.name)}">
      <span class="ms">${agy ? "rocket_launch" : "account_circle"}</span>
      <span class="acc-txt"><b>${esc(shortName(a.name))}${a.logged_in ? "" : " ⚠"}</b><small>${agy ? "Antigravity" : "Claude"}${p == null ? "" : ` · ${p} %`}</small></span>
      ${p == null ? "" : `<span class="acc-bar"><i style="width:${Math.min(100, p)}%;background:${limColor(p)}"></i></span>`}</button>`;
  }).join("");
  const tabAcc = accs.find((a) => a.id === menuTab);
  const agyTab = tabAcc?.provider === "antigravity";
  const groups = [];
  for (const id of (state.boot?.models || []).filter((x) => x.startsWith("agy:") === agyTab)) {
    const m = modelInfo(id), [base, lvl] = levelOf(m.name);
    let g = groups.find((x) => x.base === base);
    if (!g) groups.push(g = { base, m, ids: {} });
    g.ids[lvl || "-"] = id;
  }
  const list = groups.length ? groups.map((g) => {
    const ids = Object.values(g.ids);
    const on = menuTab === account?.id && ids.includes(model);
    const pick = on ? model : g.ids.medium || ids[0];
    const lv = Object.keys(LEVELS).filter((k) => g.ids[k]);
    const ef = agyTab ? [] : effortsOf(pick), cur = on ? effort || effortDefault(pick) : null;
    return `<div role="menuitem" tabindex="0" data-m="${pick}" class="mm-item ${on ? "sel" : ""}" title="${esc(g.m.desc)}">
      <span class="mic"><span class="ms">${g.m.icon}</span></span>
      <span class="mtxt"><b>${esc(g.base)}</b><small>${esc(g.m.desc)}</small></span>
      ${lv.length ? `<span class="mm-lvl" aria-label="Nachdenken">${lv.map((k) => `<button type="button" data-m="${g.ids[k]}" class="${on && model === g.ids[k] ? "on" : ""}">${LEVELS[k]}</button>`).join("")}</span>` : ef.length ? `<span class="mm-lvl claude" aria-label="Nachdenken">${ef.map((k) => `<button type="button" data-m="${pick}" data-eff="${k}" class="${cur === k ? "on" : ""}">${EFFORTS[k]}</button>`).join("")}</span>`
        : `<span class="ms mcheck">check</span>`}</div>`;
  }).join("") : `<div class="mm-empty">${agyTab ? "Noch keine Modelle – den Antigravity-Account erst anmelden (Einstellungen → Accounts)." : "Keine Modelle."}</div>`;
  $("modelMenu").innerHTML = `${accs.length > 1 ? `<div class="mm-head">Account</div><div class="mm-accs">${tabs}</div>` : ""}
    <div class="mm-head">Modell${groups.length ? " <span>· rechts wählst du, wie gründlich es nachdenkt</span>" : ""}</div><div class="mm-list">${list}</div>`;
  $("modelMenu").querySelector(".mm-acc.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function choose(model, accountId, effort = null) {
  const before = current();
  sel = { model, account: accountId, effort };
  localStorage.setItem("modelSel", JSON.stringify(sel));
  renderChip();
  const now = current();
  if (ctx.id && document.body.classList.contains("chatting") && (before.model !== now.model || before.account?.id !== now.account?.id))
    addSys(`Ab hier mit ${modelInfo(now.model).name}${now.account ? " (" + now.account.name + ")" : ""}`);
}

// ---- "/" skills ----
function renderSlash() {
  const m = /^\/([a-z0-9-]*)$/.exec($("input").value);
  slashItems = m ? (state.boot?.skills || []).filter((s) => s.id.startsWith(m[1]) || s.name.toLowerCase().startsWith(m[1])) : [];
  document.body.classList.toggle("slash-open", slashItems.length > 0);
  if (!slashItems.length) return;
  slashIdx = Math.min(slashIdx, slashItems.length - 1);
  $("slashMenu").innerHTML = slashItems.map((s, i) => `<button type="button" data-skill="${s.id}" class="${i === slashIdx ? "on" : ""}">
    <span class="ms">${esc(s.icon || "auto_fix_high")}</span><span><b>/${esc(s.id)}</b><small>${esc(s.description || s.name)}</small></span></button>`).join("");
}
function pickSlash(id) {
  $("input").value = `/${id} `;
  document.body.classList.remove("slash-open");
  $("input").focus();
  updateSend();
}

function updateSend() {
  const v = $("input").value;
  const mode = !ctx.busy ? "send" : v.trim() ? "queue" : "stop";
  const b = $("send");
  b.disabled = !v.trim() && !ctx.busy;
  b.dataset.mode = mode;
  b.querySelector(".ms").textContent = { send: "arrow_upward", queue: "playlist_add", stop: "stop" }[mode];
  b.title = { send: "", queue: "In die Warteschlange – geht raus, sobald Ida fertig ist", stop: "Antwort stoppen" }[mode];
  b.setAttribute("aria-label", { send: "Senden", queue: "In die Warteschlange", stop: "Antwort stoppen" }[mode]);
  document.body.classList.toggle("has-text", !!v);
}

export function initComposer() {
  const input = $("input");
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = input.scrollHeight + "px";
    updateSend();
    renderSlash();
  });
  input.addEventListener("keydown", (e) => {
    if (document.body.classList.contains("slash-open")) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { slashIdx = (slashIdx + (e.key === "ArrowDown" ? 1 : slashItems.length - 1)) % slashItems.length; renderSlash(); e.preventDefault(); return; }
      if (e.key === "Tab" || e.key === "Enter") { e.preventDefault(); pickSlash(slashItems[slashIdx].id); return; }
      if (e.key === "Escape") { document.body.classList.remove("slash-open"); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  $("slashMenu").addEventListener("mousedown", (e) => { const b = e.target.closest("[data-skill]"); if (b) { e.preventDefault(); pickSlash(b.dataset.skill); } });
  $("skillBtn").onclick = () => { input.value = "/"; input.focus(); slashIdx = 0; renderSlash(); updateSend(); };

  $("composer").onsubmit = (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return ctx.busy && e.submitter ? stopAnswer() : undefined; // empty field while answering: the button stops
    let skill = null, body = text;
    const m = /^\/([a-z0-9-]{1,32})(?:\s+([\s\S]*))?$/.exec(text);
    if (m && (state.boot?.skills || []).some((s) => s.id === m[1])) { skill = m[1]; body = (m[2] || "").trim(); }
    input.value = ""; input.style.height = "auto";
    updateSend();
    document.body.classList.remove("slash-open");
    const { model, account, effort } = current();
    const opts = { model, accountId: account?.id, effort };
    if (ctx.busy) enqueue(body, skill, opts); // Ida is still answering: it waits in the queue
    else send(body, { skill, ...opts });
  };

  $("modelBtn").onclick = (e) => {
    e.stopPropagation();
    const open = document.body.classList.toggle("model-open");
    if (open) { menuTab = null; renderMenu(); loadUsage(); }
  };
  $("modelMenu").onclick = (e) => {
    const t = e.target.closest("[data-tab]");
    if (t) { menuTab = t.dataset.tab; renderMenu(); return; }
    const b = e.target.closest("[data-m]");
    if (!b) return;
    document.body.classList.remove("model-open");
    choose(b.dataset.m, menuTab, b.dataset.eff || null);
    input.focus();
  };
  document.addEventListener("click", (e) => {
    const p = e.composedPath();
    if (!p.includes($("modelMenu")) && !p.includes($("modelBtn"))) document.body.classList.remove("model-open");
    if (!p.includes($("slashMenu")) && !p.includes(input)) document.body.classList.remove("slash-open");
  });
  // while an answer runs, the send button turns into a stop button
  // (with text in the field it queues the message instead)
  const busy = (on) => {
    document.body.classList.toggle("answering", on);
    updateSend();
  };
  initQueue();
  document.addEventListener("chat:busy", () => busy(true));
  document.addEventListener("chat:done", () => busy(false));
  // the chat that was opened brings its own model/account
  document.addEventListener("chat:model", (e) => { sel = { model: e.detail.model, account: e.detail.account }; renderChip(); });
  onChange(() => { renderChip(); if (document.body.classList.contains("model-open")) renderMenu(); });
  renderChip();
  loadUsage();
  setInterval(() => !document.hidden && loadUsage(), 180000);
}
