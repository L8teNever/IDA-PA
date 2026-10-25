// Einstellungen → Accounts: Claude (login in the terminal, .credentials.json or setup-token) and
// Google Antigravity (login in the terminal with the agy CLI). Name, login, remove.
import { api } from "../../api.js";
import { loadBoot } from "../../store.js";
import { $, attempt, esc } from "../../ui/dom.js";
import { subHead } from "./index.js";
import { terminalView } from "./terminal.js";

export async function render(el, id) {
  if (id) return terminalView(el, id);
  const list = await api.get("/accounts");
  el.innerHTML = subHead("Accounts", "/einstellungen") + `
    <p class="sub-intro">Jeder Account ist ein eigenes Abo mit eigenem Limit – <b>Claude</b> oder <b>Google Antigravity</b> (Gemini u. a.). Welcher Account läuft, hängt am Modell: Claude-Modelle laufen auf Claude-Accounts, <span class="mono">agy:…</span>-Modelle auf Antigravity-Accounts. Ist einer am Limit, springt IDA auf den nächsten vom selben Anbieter (wenn „Limit-Fallback“ an ist).</p>
    <div class="slist acc-list">${list.map((a) => `<div class="srow col" data-id="${a.id}">
      <div class="srow-top"><span class="sdot" style="background:${a.logged_in ? "#7ee2b8" : "#ff8f8f"}"></span>
        <span class="smain"><b>${esc(a.name)} <span class="mono dim">${a.id}</span> <span class="prov-tag ${a.provider}">${a.provider === "antigravity" ? "Antigravity" : "Claude"}</span></b><small>${a.logged_in ? (a.has_token ? "angemeldet (Token)" : "angemeldet") : "nicht angemeldet"}</small></span></div>
      <div class="acc-actions">
        <a class="es-btn primary" href="/einstellungen/accounts/${a.id}"><span class="ms">terminal</span>${a.logged_in ? "Terminal" : "Anmelden"}</a>
        <input class="mini-in" data-name value="${esc(a.name)}" maxlength="40" aria-label="Name">
        <button class="es-btn" data-act="rename"><span class="ms">edit</span>Umbenennen</button>
        ${a.provider === "antigravity" ? `<button class="es-btn" data-act="refresh"><span class="ms">refresh</span>Prüfen</button>` : `
        <label class="es-btn"><span class="ms">upload_file</span>.credentials.json<input type="file" accept=".json,application/json" data-act="upload" hidden></label>
        <button class="es-btn" data-act="token"><span class="ms">key</span>Token</button>`}
        ${a.logged_in ? `<button class="es-btn" data-act="logout"><span class="ms">logout</span>Abmelden</button>` : ""}
        <button class="es-btn danger" data-act="delete"><span class="ms">delete</span></button>
      </div></div>`).join("")}</div>
    <div class="btn-row acc-add" style="margin-top:12px">
      <button class="add-btn" data-add="claude"><span class="ms">add</span>Claude-Account</button>
      <button class="add-btn" data-add="antigravity"><span class="ms">rocket_launch</span>Antigravity-Account</button></div>
    <div class="pcard help"><b>So meldest du einen Account an</b>
      <ol><li><b>Anmelden</b> öffnet ein Terminal mit <span class="mono">claude auth login</span> – wie in T3 Code. Link antippen, bei Claude einloggen, Code ins Terminal einfügen, Enter. Die Anmeldung bleibt dauerhaft gespeichert und erneuert sich selbst.</li>
      <li>Oder die Datei <span class="mono">~/.claude/.credentials.json</span> eines eingeloggten Rechners hochladen.</li>
      <li>Oder <span class="mono">claude setup-token</span> ausführen und den Token unter „Token“ einfügen (dann fehlen evtl. die Limit-Anzeigen).</li>
      <li><b>Antigravity:</b> „Anmelden“ startet Googles <span class="mono">agy</span>. Link öffnen → mit Google anmelden → Code kopieren → „Einfügen“ → Enter. Danach öffnet agy seinen eigenen Chat – mit „Strg+C“ beenden. Die Modelle erscheinen dann im Modell-Menü.</li></ol>
      <p class="sub-intro" style="margin:10px 0 0">⚠ Google hat Konten gesperrt, deren Antigravity-Login in fremden Programmen genutzt wurde. IDA nutzt Googles eigenes <span class="mono">agy</span>, eine offizielle Freigabe gibt es aber nicht – nimm am besten ein Zweitkonto.</p></div>`;
  el.querySelectorAll("[data-add]").forEach((b) => (b.onclick = async () => {
    if (await attempt(() => api.post("/accounts", { name: "", provider: b.dataset.add }), "Account angelegt")) { await loadBoot(); render(el); }
  }));
  el.querySelectorAll("[data-id]").forEach((row) => {
    const id = row.dataset.id;
    const up = row.querySelector('[data-act="upload"]');
    if (up) up.onchange = async (e) => {
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
    if (act === "refresh") ok = await attempt(() => api.post(`/accounts/${id}/refresh`), "Geprüft");
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
