// Einstellungen → GitHub: sign in once (Device Flow: code on github.com/login/device, or a token),
// so the code area can list, clone and create repos, push and open pull requests. The token stays on the server.
import { api } from "../../api.js";
import { $, attempt, esc, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

let timer = null;

export async function render(el) {
  clearTimeout(timer);
  const [s, code] = await Promise.all([api.get("/github/status"), api.get("/code/settings")]);
  const head = subHead("GitHub", "/einstellungen");
  if (!code.enabled) {
    el.innerHTML = head + `<div class="soon-box"><span class="ms">code_off</span>Erst den <a href="/einstellungen/code">Code-Bereich</a> einschalten.</div>`;
    return;
  }
  if (s.connected) {
    el.innerHTML = head + `
      <div class="pcard gh-card"><img src="${esc(s.avatar || "")}" alt="">
        <div><b>${esc(s.name || s.login)}</b><small>@${esc(s.login)} · ${s.via === "device" ? "angemeldet über GitHub" : "per Token"}</small>
        <small class="mono">${esc((s.scopes || []).join(", ") || "–")}</small></div></div>
      <p class="sub-intro">Damit klont, erstellt und pusht IDA deine Repos und öffnet Pull Requests – Commits laufen unter deinem Namen (mit deiner GitHub-noreply-Adresse). Der Zugang bleibt auf dem Server; der Browser und die KI sehen ihn nie im Text.</p>
      <div class="es-actions"><a class="es-btn" href="/code"><span class="ms">code</span>Zum Code-Bereich</a>
        <button class="es-btn" id="ghOut"><span class="ms">logout</span>Abmelden</button></div>
      <p class="hint">Abmelden löscht den Zugang in IDA. Ganz entziehen kannst du ihn auf github.com unter Settings → Applications.</p>`;
    $("ghOut").onclick = async () => { if (confirm("Von GitHub abmelden?") && await attempt(() => api.del("/github"), "Abgemeldet")) render(el); };
    return;
  }
  const p = s.pending;
  el.innerHTML = head + `
    <p class="sub-intro">Melde dich einmal mit deinem GitHub-Konto an – dann zeigt der Code-Bereich deine Repos, und IDA kann klonen, neue Repos erstellen, committen, pushen und Pull Requests öffnen.</p>
    ${p && !p.error ? `<div class="pcard device-box">
        <small>Öffne GitHub und gib diesen Code ein:</small>
        <div class="device-code mono" id="ghCode">${esc(p.user_code)}</div>
        <div class="es-actions"><a class="es-btn primary" href="${esc(p.verification_uri)}" target="_blank" rel="noopener"><span class="ms">open_in_new</span>GitHub öffnen</a>
          <button class="es-btn" id="ghCopy"><span class="ms">content_copy</span>Code kopieren</button>
          <button class="es-btn" id="ghCancel"><span class="ms">close</span>Abbrechen</button></div>
        <p class="hint"><span class="ms spin" style="font-size:14px;vertical-align:-2px">progress_activity</span> Warte auf deine Bestätigung bei GitHub …</p></div>`
      : `${p?.error ? `<div class="ferr" style="margin-bottom:10px">${esc(p.error)}</div>` : ""}
        <button class="add-btn" id="ghLogin" ${s.client_id ? "" : "disabled"}><span class="ms">login</span>Mit GitHub anmelden</button>`}
    <label class="fld"><span>Client ID deiner GitHub OAuth App${s.client_id_env ? " (aus GITHUB_CLIENT_ID, hier überschreibbar)" : ""}</span>
      <input id="ghCid" class="mono" value="${esc(s.client_id || "")}" placeholder="Iv1.… oder Ov23…" maxlength="80"></label>
    <div class="es-actions"><button class="es-btn" id="ghCidSave"><span class="ms">save</span>Client ID speichern</button></div>
    <div class="pcard help" style="margin-top:14px"><b>Einmalig: OAuth App anlegen</b>
      <ol><li>Auf github.com: <b>Settings → Developer settings → OAuth Apps → New OAuth App</b>.</li>
      <li>Name z. B. „IDA“, Homepage und Callback-URL: die Adresse deiner IDA (wird beim Device Flow nicht benutzt).</li>
      <li>Nach dem Anlegen <b>„Enable Device Flow“</b> anhaken und speichern.</li>
      <li>Die <b>Client ID</b> oben eintragen – ein Client Secret braucht es nicht.</li></ol></div>
    <details class="pcard gh-token"><summary><b>Oder: Personal Access Token einfügen</b></summary>
      <p class="sub-intro" style="margin:8px 0">github.com → Settings → Developer settings → Personal access tokens. Rechte: <span class="mono">repo</span>, <span class="mono">workflow</span> (klassisch) bzw. Contents + Pull requests: Read and write (fine-grained).</p>
      <label class="fld"><span>Token</span><input id="ghTok" type="password" class="mono" autocomplete="off" placeholder="ghp_… / github_pat_…"></label>
      <div class="es-actions"><button class="es-btn primary" id="ghTokSave"><span class="ms">key</span>Mit Token anmelden</button></div></details>`;
  $("ghCidSave").onclick = async () => { if (await attempt(() => api.put("/github/client-id", { client_id: $("ghCid").value }), "Gespeichert")) render(el); };
  $("ghTokSave").onclick = async () => {
    const token = $("ghTok").value.trim();
    if (!token) return toast("Token fehlt");
    if (await attempt(() => api.post("/github/token", { token }), "Mit GitHub verbunden")) render(el);
  };
  if ($("ghLogin")) $("ghLogin").onclick = async () => { if (await attempt(() => api.post("/github/login"))) render(el); };
  if ($("ghCancel")) $("ghCancel").onclick = async () => { await api.del("/github/login"); render(el); };
  if ($("ghCopy")) $("ghCopy").onclick = () => navigator.clipboard?.writeText(p.user_code).then(() => toast("Code kopiert"), () => toast(p.user_code));
  if (p && !p.error) {
    // the server waits for GitHub's OK; check every 2 s whether it arrived
    const check = async () => {
      if (!document.body.contains(el) || !$("ghCode")) return;
      const now = await api.get("/github/status").catch(() => null);
      if (now?.connected) { toast(`Angemeldet als ${now.login}`); return render(el); }
      if (now && (!now.pending || now.pending.error)) return render(el);
      timer = setTimeout(check, 2000);
    };
    timer = setTimeout(check, 2000);
  }
}
