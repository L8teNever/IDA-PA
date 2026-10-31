// Einstellungen wie im Kalender und Wiki: Übersicht mit Unterseiten (Code-Bereich, GitHub, App).
import { api } from "./api.js";
import { loadBoot, loadTree, state } from "./store.js";
import { attempt, esc, openModal, toast } from "./ui.js";

let installPrompt = null;
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installPrompt = e; });

const head = (title, back) => `<div class="sheet-head set-head">
  ${back ? `<button type="button" class="icon-btn" data-set-back aria-label="Zurück"><span class="ms">arrow_back</span></button>` : ""}
  <h2>${esc(title)}</h2><button type="button" class="icon-btn" data-close aria-label="Schließen"><span class="ms">close</span></button></div>`;
const item = (go, icon, title, sub, right = `<span class="ms">chevron_right</span>`) => `<button type="button" class="set-li" ${go ? `data-set-go="${go}"` : ""}>
  <span class="ms">${icon}</span><span class="ct"><span>${esc(title)}</span>${sub ? `<small>${esc(sub)}</small>` : ""}</span>${right}</button>`;

export function openSettings(start = "home") {
  const m = openModal(`<div class="set-view" id="setView"></div>`, { onClose: () => clearTimeout(timer) });
  const el = m.el.querySelector("#setView");
  let page = start, timer = null, gh = null;

  const pages = {
    home: () => {
      const on = !!state.boot?.code_enabled;
      const ida = state.app?.ida_url;
      return `${head("Einstellungen")}<div class="sheet-scroll">
        <div class="set-gl">Code</div><div class="set-list">
          <div class="set-li static"><span class="ms">code</span><span class="ct"><span>Code-Bereich</span><small>${on ? "Ida darf in deinen Projekten arbeiten" : "Ausgeschaltet"}</small></span>
            <button type="button" class="sw ${on ? "on" : ""}" id="setEnabled" aria-label="Code-Bereich ein/aus"></button></div>
          ${item("github", "account_circle", "GitHub", gh?.connected ? `Angemeldet als ${gh.login}` : "Nicht verbunden")}
        </div>
        <div class="set-gl">KI</div><div class="set-list">
          ${item("", "psychology", "Modelle & Accounts", `${(state.boot?.accounts || []).length} Account${(state.boot?.accounts || []).length === 1 ? "" : "s"} aus IDA · im Thread unten wählbar`, "")}
        </div>
        <div class="set-gl">App</div><div class="set-list">
          ${installPrompt ? item("install", "install_desktop", "App installieren", "Als eigene App auf diesem Gerät", "") : ""}
          ${ida ? `<a class="set-li" href="${esc(ida)}" target="_blank" rel="noopener"><span class="ms">open_in_new</span><span class="ct"><span>Zu IDA</span><small>Accounts, Agents und alles andere</small></span></a>` : ""}
          ${item("reload", "refresh", "Neu laden", "Die neueste Version holen", "")}
        </div></div>`;
    },
    github: () => {
      const s = gh || {};
      if (!state.boot?.code_enabled) return `${head("GitHub", true)}<p class="sheet-hint">Erst den Code-Bereich einschalten.</p>`;
      if (s.connected) {
        return `${head("GitHub", true)}<div class="sheet-scroll"><div class="set-list">
          <div class="set-li static">${s.avatar ? `<img class="gh-av" src="${esc(s.avatar)}" alt="">` : `<span class="ms">account_circle</span>`}
            <span class="ct"><span>${esc(s.name || s.login)}</span><small>@${esc(s.login)} · ${s.via === "device" ? "angemeldet über GitHub" : "per Token"}</small></span></div></div>
          <p class="sheet-hint">Damit klont, erstellt und pusht Ida deine Repos und öffnet Pull Requests. Der Zugang bleibt auf dem Server – Browser und KI sehen ihn nie.</p>
          <div class="sheet-actions"><button type="button" class="btn-danger" id="ghOut"><span class="ms">logout</span>Abmelden</button></div></div>`;
      }
      const p = s.pending;
      return `${head("GitHub", true)}<div class="sheet-scroll">
        <p class="sheet-hint">Melde dich einmal mit GitHub an – dann siehst du deine Repos, und Ida kann klonen, neue Repos erstellen, committen, pushen und Pull Requests öffnen.</p>
        ${p && !p.error ? `<div class="device-box"><small>Öffne GitHub und gib diesen Code ein:</small><div class="device-code" id="ghCode">${esc(p.user_code)}</div>
            <div class="sheet-actions"><a class="btn-primary" href="${esc(p.verification_uri)}" target="_blank" rel="noopener"><span class="ms">open_in_new</span>GitHub öffnen</a>
            <button type="button" class="btn-tonal" id="ghCopy"><span class="ms">content_copy</span>Kopieren</button>
            <button type="button" class="btn-tonal" id="ghCancel">Abbrechen</button></div>
            <p class="sheet-hint"><span class="ms spin sm">progress_activity</span> Warte auf deine Bestätigung bei GitHub …</p></div>`
          : `${p?.error ? `<div class="ferr">${esc(p.error)}</div>` : ""}
            <button type="button" class="btn-primary big" id="ghLogin" ${s.client_id ? "" : "disabled"}><span class="ms">login</span>Mit GitHub anmelden</button>
            ${s.client_id ? "" : `<p class="sheet-hint">Dafür braucht es die Client ID einer GitHub OAuth App (mit „Device Flow“) – unten eintragen.</p>`}`}
        <div class="set-gl">Client ID der OAuth App</div>
        <form class="inline-form" id="ghCidForm"><input class="cd-input mono" name="cid" value="${esc(s.client_id || "")}" placeholder="Iv1.… oder Ov23…" maxlength="80"><button class="btn-tonal" type="submit">Speichern</button></form>
        <div class="set-gl">Oder: Personal Access Token</div>
        <form class="inline-form" id="ghTokForm"><input class="cd-input mono" name="tok" type="password" placeholder="ghp_… / github_pat_…" autocomplete="off"><button class="btn-tonal" type="submit">Anmelden</button></form>
        <p class="sheet-hint">Rechte: <code>repo</code>, <code>workflow</code> (klassisch) bzw. Contents + Pull requests: Read and write.</p></div>`;
    },
  };

  const show = async (name, dir = "") => {
    page = name;
    clearTimeout(timer);
    if (name === "home" || name === "github") gh = await api.get("/github/status").catch(() => gh);
    if (!el.isConnected) return;
    el.className = `set-view ${dir}`;
    el.innerHTML = pages[name]();
    if (name === "github" && gh?.pending && !gh.pending.error) {
      const check = async () => {
        if (!el.isConnected || page !== "github") return;
        const now = await api.get("/github/status").catch(() => null);
        if (now?.connected) { toast(`Angemeldet als ${now.login}`); loadTree().catch(() => {}); return show("github"); }
        if (now && (!now.pending || now.pending.error)) return show("github");
        timer = setTimeout(check, 2000);
      };
      timer = setTimeout(check, 2000);
    }
  };

  el.addEventListener("click", async (e) => {
    const go = e.target.closest("[data-set-go]")?.dataset.setGo;
    if (e.target.closest("[data-set-back]")) return show("home", "back");
    if (go === "github") return show("github", "fwd");
    if (go === "install" && installPrompt) { installPrompt.prompt(); installPrompt = null; return show("home"); }
    if (go === "reload") { location.reload(); return; }
    const t = e.target;
    if (t.closest("#setEnabled")) {
      const on = !state.boot?.code_enabled;
      if (await attempt(() => api.put("/code/settings", { enabled: on }), on ? "Code-Bereich an" : "Code-Bereich aus")) {
        await loadBoot();
        await loadTree().catch(() => {});
        show("home");
      }
    }
    if (t.closest("#ghLogin")) { if (await attempt(() => api.post("/github/login"))) show("github"); }
    if (t.closest("#ghCancel")) { await api.del("/github/login").catch(() => {}); show("github"); }
    if (t.closest("#ghCopy")) navigator.clipboard?.writeText(gh?.pending?.user_code || "").then(() => toast("Code kopiert"), () => toast(gh?.pending?.user_code || ""));
    if (t.closest("#ghOut")) { if (await attempt(() => api.del("/github"), "Abgemeldet")) { loadTree().catch(() => {}); show("github"); } }
  });
  el.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (e.target.id === "ghCidForm") { if (await attempt(() => api.put("/github/client-id", { client_id: e.target.elements.cid.value.trim() }), "Gespeichert")) show("github"); }
    if (e.target.id === "ghTokForm") {
      const token = e.target.elements.tok.value.trim();
      if (!token) return toast("Token fehlt");
      if (await attempt(() => api.post("/github/token", { token }), "Mit GitHub verbunden")) { loadTree().catch(() => {}); show("github"); }
    }
  });
  show(start);
}
