// Einstellungen → Claude-Accounts: name, login (upload .credentials.json or paste a setup-token), remove.
import { api } from "../../api.js";
import { loadBoot } from "../../store.js";
import { $, attempt, closeSheet, esc, openSheet, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

export async function render(el) {
  const list = await api.get("/accounts");
  el.innerHTML = subHead("Claude-Accounts", "/einstellungen") + `
    <p class="sub-intro">Jeder Account ist ein eigenes Claude-Abo mit eigenem Limit. Ist einer am Limit, nehmen Routinen und Agents automatisch den nächsten (wenn „Limit-Fallback“ an ist).</p>
    <div class="slist">${list.map((a) => `<div class="srow col" data-id="${a.id}">
      <div class="srow-top"><span class="sdot" style="background:${a.logged_in ? "#7ee2b8" : "#ff8f8f"}"></span>
        <span class="smain"><b>${esc(a.name)} <span class="mono dim">${a.id}</span></b><small>${a.logged_in ? (a.has_token ? "angemeldet (Token)" : "angemeldet") : "nicht angemeldet"}</small></span></div>
      <div class="acc-actions">
        <button class="es-btn primary" data-act="browser"><span class="ms">login</span>Im Browser anmelden</button>
        <input class="mini-in" data-name value="${esc(a.name)}" maxlength="40" aria-label="Name">
        <button class="es-btn" data-act="rename"><span class="ms">edit</span>Umbenennen</button>
        <label class="es-btn"><span class="ms">upload_file</span>.credentials.json<input type="file" accept=".json,application/json" data-act="upload" hidden></label>
        <button class="es-btn" data-act="token"><span class="ms">key</span>Token</button>
        ${a.logged_in ? `<button class="es-btn" data-act="logout"><span class="ms">logout</span>Abmelden</button>` : ""}
        <button class="es-btn danger" data-act="delete"><span class="ms">delete</span></button>
      </div></div>`).join("")}</div>
    <button class="add-btn" id="accAdd" style="margin-top:12px"><span class="ms">add</span>Account hinzufügen</button>
    <div class="pcard help"><b>So meldest du einen Account an</b>
      <ol><li><b>Im Browser anmelden</b> (am einfachsten): Link öffnen, bei Claude einloggen, den angezeigten Code hier einfügen. Die Anmeldung bleibt gespeichert und wird automatisch erneuert.</li>
      <li>Oder die Datei <span class="mono">~/.claude/.credentials.json</span> eines eingeloggten Rechners hochladen.</li>
      <li>Oder <span class="mono">claude setup-token</span> ausführen und den Token unter „Token“ einfügen (dann fehlen evtl. die Limit-Anzeigen).</li></ol></div>`;
  $("accAdd").onclick = async () => { if (await attempt(() => api.post("/accounts", { name: "" }), "Account angelegt")) { await loadBoot(); render(el); } };
  el.querySelectorAll("[data-id]").forEach((row) => {
    const id = row.dataset.id;
    row.querySelector('[data-act="upload"]').onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      if (await attempt(() => api.post(`/accounts/${id}/credentials`, fd), "Angemeldet")) { await loadBoot(); render(el); }
    };
  });
  el.onclick = async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const row = b.closest("[data-id]"), id = row.dataset.id;
    const act = b.dataset.act;
    let ok;
    if (act === "browser") return browserLogin(id, list.find((a) => a.id === id)?.name || id, () => render(el));
    if (act === "rename") ok = await attempt(() => api.patch(`/accounts/${id}`, { name: row.querySelector("[data-name]").value }), "Gespeichert");
    if (act === "token") {
      const t = prompt("Token aus „claude setup-token“ einfügen (leer = Token entfernen):");
      if (t === null) return;
      ok = await attempt(() => api.patch(`/accounts/${id}`, { oauth_token: t }), t ? "Token gespeichert" : "Token entfernt");
    }
    if (act === "logout" && confirm("Login dieses Accounts entfernen?")) ok = await attempt(() => api.del(`/accounts/${id}/login`), "Abgemeldet");
    if (act === "delete" && confirm("Account entfernen?")) ok = await attempt(() => api.del(`/accounts/${id}`), "Entfernt");
    if (ok) { await loadBoot(); render(el); }
  };
}

// Browser login: the server starts `claude auth login`, a new tab opens Claude. After "Autorisieren" Claude shows a
// code ("<code>#<state>"). As soon as the user copies it and comes back, we read it from the clipboard, recognise it
// by the state of exactly this login and finish automatically – no typing, no extra button.
async function browserLogin(id, name, done) {
  const win = window.open("about:blank", "_blank"); // opened right away, so pop-up blockers allow it
  let url;
  try {
    url = (await api.post(`/accounts/${id}/login/start`)).url;
  } catch (e) {
    if (win) win.close();
    return toast(e.message);
  }
  if (win) win.location.href = url;
  const state = new URL(url).searchParams.get("state") || "";
  // Claude's code looks like "<code>#<state>"; the state belongs to exactly this login
  const isCode = (t) => {
    const [code, st, ...rest] = String(t || "").trim().split("#");
    return !!state && !rest.length && st === state && /^[\w-]{10,}$/.test(code);
  };
  let busy = false, finished = false;

  const cleanup = () => {
    finished = true;
    removeEventListener("focus", onBack);
    document.removeEventListener("visibilitychange", onBack);
  };
  const submit = async (code) => {
    if (busy || finished) return;
    busy = true;
    draw("checking");
    try {
      await api.post(`/accounts/${id}/login/code`, { code: code.trim() });
      cleanup(); closeSheet(); toast(`${name} ist angemeldet`); await loadBoot(); done();
    } catch (e) {
      cleanup(); draw("error", e.message);
    } finally { busy = false; }
  };
  // back from the Claude tab: look into the clipboard (the browser may ask once for permission)
  async function onBack() {
    if (finished || busy || document.hidden || !navigator.clipboard?.readText) return;
    try {
      const t = await navigator.clipboard.readText();
      if (isCode(t)) submit(t);
    } catch { /* no permission – the paste field still works */ }
  }
  addEventListener("focus", onBack);
  document.addEventListener("visibilitychange", onBack);

  function draw(mode = "wait", err = "") {
    openSheet(`<div class="sub-head" style="padding-top:0"><h1 style="font-size:22px">${esc(name)} anmelden</h1>
        <button class="icon-btn es-x" id="lgClose" aria-label="Schließen"><span class="ms">close</span></button></div>
      ${mode === "checking" ? `<div class="login-wait"><span class="ms spin">progress_activity</span>Anmeldung wird gespeichert …</div>` : mode === "error" ? `
      <div class="ferr" style="margin-bottom:12px">${esc(err)}</div>
      <div class="wiz-nav"><button class="es-btn" id="lgCancel">Schließen</button><button class="es-btn primary" id="lgRetry"><span class="ms">refresh</span>Nochmal versuchen</button></div>` : `
      <ol class="login-steps">
        <li>Im Claude-Tab einloggen und <b>„Autorisieren“</b> tippen.</li>
        <li>Auf der Code-Seite auf <b>„Kopieren“</b> tippen.</li>
        <li>Zurück zu IDA – der Rest passiert automatisch.</li>
      </ol>
      <div class="login-wait"><span class="ms spin">progress_activity</span>Warte auf die Bestätigung bei Claude …</div>
      <details class="login-manual"><summary>Klappt nicht automatisch?</summary>
        <label class="fld"><span>Code hier einfügen</span><input id="lgCode" autocomplete="off" spellcheck="false"></label>
        <a href="${esc(url)}" target="_blank" rel="noopener">Claude-Seite erneut öffnen</a></details>
      <div class="wiz-nav"><button class="es-btn" id="lgCancel">Abbrechen</button></div>`}`);
    const cancel = () => { cleanup(); closeSheet(); api.post(`/accounts/${id}/login/cancel`).catch(() => {}); };
    if ($("lgClose")) $("lgClose").onclick = cancel;
    if ($("lgCancel")) $("lgCancel").onclick = cancel;
    if ($("lgRetry")) $("lgRetry").onclick = () => { closeSheet(); browserLogin(id, name, done); };
    // pasting (or typing) the code submits it immediately
    if ($("lgCode")) $("lgCode").addEventListener("input", (e) => { if (isCode(e.target.value)) submit(e.target.value); });
  }
  draw();
}
