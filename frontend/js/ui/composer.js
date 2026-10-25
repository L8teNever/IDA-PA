// Input at the bottom: auto-growing text field, model/account menu with limits, "/" skill menu, send.
import { ctx, send, addSys } from "../pages/chat.js";
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
  return { model, account };
}

function renderChip() {
  const { model, account } = current();
  if (!model) return;
  const m = modelInfo(model);
  $("modelIcon").textContent = m.icon;
  $("modelName").textContent = m.short;
  $("modelBtn").querySelectorAll(".acc-tag").forEach((x) => x.remove());
  if (account && accounts().length > 1) $("modelName").insertAdjacentHTML("afterend", accTag(account));
  const p = account ? state.usage[account.id] : null;
  $("limNow").innerHTML = p == null ? "" : `${p} %${ring(p, "small")}`;
}

let menuTab = null;
function renderMenu() {
  const accs = accounts();
  const { model, account } = current();
  if (!menuTab || !accs.some((a) => a.id === menuTab)) menuTab = account?.id || accs[0]?.id;
  const rail = accs.map((a) => `<button type="button" data-tab="${a.id}" class="${a.id === menuTab ? "on" : ""}">
      <span class="rail-ic acc">${ring(state.usage[a.id], "rail")}<span class="ms">${a.provider === "antigravity" ? "rocket_launch" : "account_circle"}</span><em>${esc(a.id.replace(/^a/, ""))}</em></span>
      <span class="rail-name">${esc(a.name)}${a.logged_in ? "" : " ⚠"}</span></button>`).join("");
  const tabAcc = accs.find((a) => a.id === menuTab);
  const agyTab = tabAcc?.provider === "antigravity";
  const models = (state.boot?.models || []).filter((id) => id.startsWith("agy:") === agyTab);
  const list = models.length ? models.map((id) => {
    const m = modelInfo(id);
    const on = id === model && menuTab === account?.id;
    return `<div role="menuitem" tabindex="0" data-m="${id}" class="mm-item ${on ? "sel" : ""}" title="${esc(m.desc)}">
      <span class="mtxt"><b>${esc(m.name)}</b><small>${esc(m.desc)}</small></span><span class="ms mcheck">check</span></div>`;
  }).join("") : `<div class="mm-empty">${agyTab ? "Noch keine Modelle – den Antigravity-Account erst anmelden (Einstellungen → Accounts)." : "Keine Modelle."}</div>`;
  $("modelMenu").innerHTML = `<div class="mm-body"><div class="mm-rail">${rail}</div><div class="mm-list">${list}</div></div>`;
}

function choose(model, accountId) {
  const before = current();
  sel = { model, account: accountId };
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
  $("send").disabled = !v.trim();
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
    if (!text) return;
    let skill = null, body = text;
    const m = /^\/([a-z0-9-]{1,32})(?:\s+([\s\S]*))?$/.exec(text);
    if (m && (state.boot?.skills || []).some((s) => s.id === m[1])) { skill = m[1]; body = (m[2] || "").trim(); }
    input.value = ""; input.style.height = "auto";
    updateSend();
    document.body.classList.remove("slash-open");
    const { model, account } = current();
    send(body, { skill, model, accountId: account?.id });
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
    choose(b.dataset.m, menuTab);
    input.focus();
  };
  document.addEventListener("click", (e) => {
    const p = e.composedPath();
    if (!p.includes($("modelMenu")) && !p.includes($("modelBtn"))) document.body.classList.remove("model-open");
    if (!p.includes($("slashMenu")) && !p.includes(input)) document.body.classList.remove("slash-open");
  });
  // the chat that was opened brings its own model/account
  document.addEventListener("chat:model", (e) => { sel = { model: e.detail.model, account: e.detail.account }; renderChip(); });
  onChange(() => { renderChip(); if (document.body.classList.contains("model-open")) renderMenu(); });
  renderChip();
  loadUsage();
  setInterval(() => !document.hidden && loadUsage(), 180000);
}
