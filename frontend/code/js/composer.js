// Eingabe unten im Thread: wachsendes Textfeld, Dateien anhängen, Modell/Account/Denktiefe wählen (wie in IDA), Senden/Stoppen.
import { api } from "./api.js";
import { ctx, send, stopAnswer } from "./chat.js";
import { loadUsage, mainAgent, modelInfo, state } from "./store.js";
import { $, esc, toast } from "./ui.js";

let sel = JSON.parse(localStorage.getItem("code-model") || "null"); // { model, account, effort }
let files = []; // { key, name, kind, url, id?, state: "up"|"ok" }
let seq = 0;

const accounts = () => state.boot?.accounts || [];
const limColor = (p) => (p >= 90 ? "#ff8f8f" : p >= 75 ? "#ffb86b" : "var(--accent)");
const EFFORTS = { low: "Niedrig", medium: "Mittel", high: "Hoch", xhigh: "Extra", max: "Max" };
const LEVELS = { low: "Niedrig", medium: "Mittel", high: "Hoch" };
function effortsOf(id) {
  if (/haiku-4-5|sonnet-4-5|-3-|-4-1|-4-0|-4$/.test(id)) return [];
  if (/opus-4-5/.test(id)) return ["low", "medium", "high"];
  if (/-4-6/.test(id)) return ["low", "medium", "high", "max"];
  return Object.keys(EFFORTS);
}
const effortDefault = (id) => (/opus-5-5|haiku-5-5/.test(id) ? "medium" : "high");
const levelOf = (name) => { const m = /^(.*) \((low|medium|high)\)$/i.exec(name); return m ? [m[1], m[2].toLowerCase()] : [name, null]; };

// gewählt: Modell, Account (muss das Modell können) und Denktiefe
export function current() {
  const models = state.boot?.models || [];
  const accs = accounts();
  const a = mainAgent();
  const model = models.includes(sel?.model) ? sel.model : a?.model || state.boot?.default_model;
  const fits = (x) => x && (x.provider === "antigravity") === String(model || "").startsWith("agy:");
  const wanted = accs.find((x) => x.id === sel?.account) || accs.find((x) => x.id === a?.account_id);
  const account = fits(wanted) ? wanted : accs.find(fits) || wanted || accs[0];
  const effort = effortsOf(model || "").includes(sel?.effort) && !String(model).startsWith("agy:") ? sel.effort : null;
  return { model, account, effort };
}

export function composerHtml(placeholder) {
  return `<form class="composer" id="composer" autocomplete="off">
    <div class="att-list" id="attList" hidden></div>
    <textarea id="input" rows="1" placeholder="${esc(placeholder)}" aria-label="Nachricht"></textarea>
    <div class="comp-row">
      <button type="button" class="icon-btn sm" id="attachBtn" title="Datei oder Bild anhängen" aria-label="Datei anhängen"><span class="ms">attach_file</span></button>
      <input type="file" id="fileInput" multiple hidden>
      <button type="button" class="model-chip" id="modelBtn" aria-haspopup="menu"><span class="ms" id="modelIcon">psychology</span><span id="modelName">Modell</span><span id="limNow" class="lim-now"></span><span class="ms dd-chev">expand_more</span></button>
      <span class="comp-space"></span>
      <button class="send" id="send" type="submit" disabled aria-label="Senden"><span class="ms">arrow_upward</span></button>
    </div>
  </form>`;
}

function renderChip() {
  const { model, account, effort } = current();
  if (!$("modelBtn")) return;
  const m = modelInfo(model);
  $("modelIcon").textContent = m.icon;
  $("modelName").textContent = (model ? m.name : "Modell") + (effort ? ` · ${EFFORTS[effort]}` : "") + (account && accounts().length > 1 ? ` · ${account.name.split("@")[0]}` : "");
  const p = account ? state.usage[account.id] : null;
  $("limNow").innerHTML = p == null ? "" : `<i style="--p:${p};--c:${limColor(p)}"></i>${p} %`;
}

function updateSend() {
  const input = $("input"), b = $("send");
  if (!input || !b) return;
  const content = !!input.value.trim() || files.some((x) => x.state === "ok");
  const mode = ctx.busy ? (content ? "wait" : "stop") : "send";
  b.disabled = mode === "wait" || (!content && !ctx.busy);
  b.dataset.mode = mode;
  b.querySelector(".ms").textContent = mode === "stop" ? "stop" : "arrow_upward";
  b.setAttribute("aria-label", mode === "stop" ? "Antwort stoppen" : "Senden");
  b.title = mode === "stop" ? "Antwort stoppen" : mode === "wait" ? "Erst wenn die Antwort fertig ist" : "Senden (Enter)";
}

// ---- Modell-Menü ----
let menuTab = null;
function openModelMenu() {
  const old = document.querySelector(".mm");
  if (old) { old.remove(); return; }
  const m = document.createElement("div");
  m.className = "mm pop show";
  document.body.appendChild(m);
  const place = () => {
    const r = $("modelBtn").getBoundingClientRect();
    m.style.left = `${Math.max(8, Math.min(r.left, innerWidth - m.offsetWidth - 8))}px`;
    m.style.bottom = `${innerHeight - r.top + 8}px`;
    m.style.maxHeight = `${r.top - 20}px`;
  };
  const draw = () => {
    const accs = accounts();
    const { model, account, effort } = current();
    if (!menuTab || !accs.some((a) => a.id === menuTab)) menuTab = account?.id || accs[0]?.id;
    const tabAcc = accs.find((a) => a.id === menuTab);
    const agyTab = tabAcc?.provider === "antigravity";
    const groups = [];
    for (const id of (state.boot?.models || []).filter((x) => x.startsWith("agy:") === agyTab)) {
      const info = modelInfo(id), [base, lvl] = levelOf(info.name);
      let g = groups.find((x) => x.base === base);
      if (!g) groups.push(g = { base, info, ids: {} });
      g.ids[lvl || "-"] = id;
    }
    m.innerHTML = `${accs.length > 1 ? `<div class="pop-head">Account</div><div class="mm-accs">${accs.map((a) => {
      const p = state.usage[a.id];
      return `<button type="button" class="mm-acc ${a.id === menuTab ? "on" : ""}" data-tab="${esc(a.id)}"><span class="ms">${a.provider === "antigravity" ? "rocket_launch" : "account_circle"}</span>
        <span class="mm-acc-txt"><b>${esc(a.name.split("@")[0])}${a.logged_in ? "" : " ⚠"}</b><small>${a.provider === "antigravity" ? "Antigravity" : "Claude"}${p == null ? "" : ` · ${p} %`}</small></span>
        ${p == null ? "" : `<span class="mm-bar"><i style="width:${Math.min(100, p)}%;background:${limColor(p)}"></i></span>`}</button>`;
    }).join("")}</div>` : ""}
      <div class="pop-head">Modell</div>
      ${groups.length ? groups.map((g) => {
        const ids = Object.values(g.ids);
        const on = menuTab === account?.id && ids.includes(model);
        const pick = on ? model : g.ids.medium || ids[0];
        const lv = Object.keys(LEVELS).filter((k) => g.ids[k]);
        const ef = agyTab ? [] : effortsOf(pick);
        const cur = on ? effort || effortDefault(pick) : null;
        return `<div class="mm-item ${on ? "sel" : ""}" data-m="${esc(pick)}" role="menuitem" tabindex="0">
          <span class="ms mm-ic">${g.info.icon}</span><span class="pop-txt"><b>${esc(g.base)}</b><small>${esc(g.info.desc)}</small></span>
          ${lv.length ? `<span class="mm-lvl">${lv.map((k) => `<button type="button" data-m="${esc(g.ids[k])}" class="${on && model === g.ids[k] ? "on" : ""}">${LEVELS[k]}</button>`).join("")}</span>`
            : ef.length ? `<span class="mm-lvl">${ef.map((k) => `<button type="button" data-m="${esc(pick)}" data-eff="${k}" class="${cur === k ? "on" : ""}">${EFFORTS[k]}</button>`).join("")}</span>`
            : on ? `<span class="ms pop-check">check</span>` : ""}</div>`;
      }).join("") : `<div class="pane-empty">${agyTab ? "Noch keine Modelle – den Antigravity-Account in IDA anmelden." : "Keine Modelle."}</div>`}`;
    place();
  };
  draw();
  loadUsage().then(() => m.isConnected && draw());
  m.onclick = (e) => {
    const t = e.target.closest("[data-tab]");
    if (t) { menuTab = t.dataset.tab; draw(); return; }
    const b = e.target.closest("[data-m]");
    if (!b) return;
    sel = { model: b.dataset.m, account: menuTab, effort: b.dataset.eff || null };
    localStorage.setItem("code-model", JSON.stringify(sel));
    close();
    renderChip();
    $("input")?.focus();
  };
  const close = () => { m.remove(); document.removeEventListener("mousedown", outside, true); removeEventListener("keydown", esc_, true); };
  const outside = (e) => { if (!m.contains(e.target) && !$("modelBtn")?.contains(e.target)) close(); };
  const esc_ = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  setTimeout(() => document.addEventListener("mousedown", outside, true));
  addEventListener("keydown", esc_, true);
}

// ---- Dateien anhängen (hochladen sofort, beim Senden gehen nur die IDs mit) ----
async function shrink(file) {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) return file;
  const bmp = await createImageBitmap(file).catch(() => null);
  if (!bmp) return file;
  const scale = Math.min(1, 2048 / Math.max(bmp.width, bmp.height));
  if (scale === 1 && file.size < 1.5 * 1024 * 1024) return file;
  const c = document.createElement("canvas");
  c.width = Math.round(bmp.width * scale);
  c.height = Math.round(bmp.height * scale);
  c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
  const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.85));
  return blob ? new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file;
}
const MAX_FILE = 100 * 1024 * 1024; // so viel nimmt Ida (und Cloudflare) pro Datei
const FILE_ICON = { pdf: "picture_as_pdf", text: "description", file: "draft" };
async function addFiles(list) {
  for (const raw of [...list]) {
    if (files.length >= 10) { toast("Höchstens 10 Dateien pro Nachricht"); break; }
    if (raw.size > MAX_FILE) { toast(`„${raw.name}“ ist zu groß (höchstens 100 MB)`); continue; }
    const image = raw.type.startsWith("image/");
    const it = { key: ++seq, name: raw.name || (image ? "bild.png" : "datei"), kind: image ? "image" : /pdf/.test(raw.type) ? "pdf" : "file", url: image ? URL.createObjectURL(raw) : null, state: "up" };
    files.push(it);
    renderFiles();
    try {
      const f = await shrink(raw);
      const fd = new FormData();
      fd.append("file", f, f.name || it.name);
      const r = await api.post("/uploads?any=true", fd); // jedes Format: Ida legt es Claude in den Projektordner
      if (!files.includes(it)) { api.del(`/uploads/${r.id}`).catch(() => {}); continue; }
      Object.assign(it, { id: r.id, name: r.name, kind: r.kind, state: "ok" });
    } catch (e) {
      toast(e.message || "Hochladen fehlgeschlagen");
      files = files.filter((x) => x !== it);
    }
    renderFiles();
  }
}
function renderFiles() {
  const box = $("attList");
  if (!box) return;
  box.hidden = !files.length;
  box.innerHTML = files.map((x) => `<span class="att ${x.state === "up" ? "up" : ""}" title="${esc(x.name)}">
    ${x.url ? `<img src="${x.url}" alt="">` : `<span class="ms">${FILE_ICON[x.kind] || "draft"}</span><span class="att-name">${esc(x.name)}</span>`}
    <button type="button" data-rm="${x.key}" aria-label="${esc(x.name)} entfernen"><span class="ms">close</span></button></span>`).join("");
  updateSend();
}
export function dropFiles() {
  files.forEach((x) => { if (x.id) api.del(`/uploads/${x.id}`).catch(() => {}); if (x.url) URL.revokeObjectURL(x.url); });
  files = [];
}

// nach dem Zeichnen der Thread-Ansicht aufrufen
export function bindComposer() {
  const input = $("input"), form = $("composer");
  const grow = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, innerHeight * 0.4)}px`; };
  input.addEventListener("input", () => { grow(); updateSend(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); form.requestSubmit(); }
  });
  input.addEventListener("paste", (e) => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); addFiles(fs); } });
  form.addEventListener("dragover", (e) => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); form.classList.add("drop"); } });
  form.addEventListener("dragleave", () => form.classList.remove("drop"));
  form.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) { e.preventDefault(); addFiles(e.dataTransfer.files); } form.classList.remove("drop"); });
  $("attachBtn").onclick = () => $("fileInput").click();
  $("fileInput").onchange = () => { addFiles($("fileInput").files); $("fileInput").value = ""; };
  $("attList").onclick = (e) => {
    const b = e.target.closest("[data-rm]");
    if (!b) return;
    const it = files.find((x) => x.key === +b.dataset.rm);
    files = files.filter((x) => x !== it);
    if (it?.id) api.del(`/uploads/${it.id}`).catch(() => {});
    if (it?.url) URL.revokeObjectURL(it.url);
    renderFiles();
  };
  $("modelBtn").onclick = openModelMenu;
  form.onsubmit = (e) => {
    e.preventDefault();
    if (ctx.busy) { if ($("send").dataset.mode === "stop") stopAnswer(); return; }
    if (files.some((x) => x.state === "up")) return toast("Moment – die Dateien werden noch hochgeladen");
    const text = input.value.trim();
    const ready = files.filter((x) => x.state === "ok").map(({ id, name, kind }) => ({ id, name, kind }));
    if (!text && !ready.length) return;
    files.forEach((x) => x.url && URL.revokeObjectURL(x.url));
    files = [];
    renderFiles();
    input.value = "";
    grow();
    const { model, account, effort } = current();
    send(text, { model, accountId: account?.id, effort, files: ready });
  };
  renderFiles();
  renderChip();
  updateSend();
}

document.addEventListener("code:busy", updateSend);
document.addEventListener("code:done", updateSend);
// ein geöffneter Thread bringt sein Modell/seinen Account mit
document.addEventListener("code:model", (e) => {
  if (e.detail?.model) sel = { model: e.detail.model, account: e.detail.account, effort: sel?.effort || null };
  renderChip();
});
export const refreshChip = renderChip;
