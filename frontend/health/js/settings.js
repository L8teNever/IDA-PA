// IDA Health – Einstellungen im Browser (alles, was sonst in der .env stünde) und die Anmeldung mit dem Einrichtungs-Code.
import { api } from "/static/shared/dashboard/api.js";
import { $, attempt, confirmDialog, esc, toast } from "/static/shared/dashboard/ui.js";

const SRC = { browser: ["Im Browser gesetzt", "ok"], env: ["aus .env", ""], standard: ["Standard", ""] };
const STATUS = (ok, text, icon) => `<div class="hp-stat ${ok ? "ok" : "warn"}"><span class="ms">${icon || (ok ? "check_circle" : "error")}</span><span>${text}</span></div>`;

// ---- Anmeldung mit Einrichtungs-Code (erste Einrichtung / Notfall) ----
export function loginView(app, me, onDone) {
  app.innerHTML = `<main class="hp-login"><div class="hp-login-card">
    <h1><span class="ms">monitor_heart</span>IDA Health</h1>
    ${me.setup ? `<p>Health ist noch nicht abgesichert. Melde dich mit dem <b>Einrichtungs-Code</b> an und richte unter <b>Einstellungen</b> alles ein –
        danach geht es über Cloudflare Access.</p>` : `<p>Du bist nicht angemeldet. Öffne Health über die Adresse mit Cloudflare Access davor – oder melde dich mit dem
        Einrichtungs-Code an (Notfall-Zugang).</p>`}
    <form id="lgF" class="hp-form"><label>Einrichtungs-Code<input class="cd-input mono" id="lgCode" autocomplete="off" autofocus placeholder="xxxxxxxx-xxxxxxxx-xxxxxxxx-xxxxxxxx"></label>
      <button class="btn-primary"><span class="ms">login</span>Anmelden</button><div class="ferr" id="lgErr"></div></form>
    <details class="hp-help"><summary>Wo steht der Code?</summary>
      <p>Im Log des Dienstes <code>health</code> (bei jedem Start neu), z. B. im IDA Dashboard → Stack <b>ida</b> → <b>health</b> → Logs, oder auf dem Server:
      <code>docker logs ida-health-1 2>&amp;1 | grep Einrichtungs-Code</code>. Außerdem in <code>/data/setup-code.txt</code> im Volume <code>health-data</code>.</p></details>
  </div></main>`;
  $("lgF").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api.post("/setup/login", { code: $("lgCode").value });
      onDone();
    } catch (x) { $("lgErr").textContent = x.message; }
  };
}

// ---- Einstellungen ----
export async function settingsView(app, topbar, me) {
  app.innerHTML = topbar("Einstellungen", { back: true }) + `<main class="page" id="stBody"><div class="muted pad">Lade …</div></main>`;
  const body = $("stBody");
  let v;
  try { v = await api.get("/settings"); } catch (x) { body.innerHTML = `<div class="hint"><span class="ms">error</span>${esc(x.message)}</div>`; return {}; }

  const field = (f) => {
    const [srcTxt, srcCls] = SRC[f.source] || ["", ""];
    const badge = `<span class="pill ${srcCls}">${srcTxt}</span>${f.source === "browser" ? `<button type="button" class="hp-reset" data-reset="${f.key}" title="Zurück auf .env/Standard">zurücksetzen</button>` : ""}`;
    let input;
    if (f.type === "bool") input = `<label class="hp-check"><input type="checkbox" data-k="${f.key}" ${f.value ? "checked" : ""}> an</label>`;
    else if (f.type === "secret") input = `<input class="cd-input mono" type="password" data-k="${f.key}" autocomplete="new-password" placeholder="${f.set ? `gespeichert (${esc(f.hint)}) – leer lassen = behalten` : "nicht gesetzt"}">`;
    else if (f.type === "int") input = `<input class="cd-input" type="number" data-k="${f.key}" value="${esc(f.value)}" min="${f.min ?? ""}" max="${f.max ?? ""}">`;
    else input = `<input class="cd-input ${f.type === "url" ? "mono" : ""}" type="${f.type === "url" ? "url" : "text"}" data-k="${f.key}" value="${esc(f.value)}" spellcheck="false">`;
    return `<div class="hp-field"><div class="hp-field-head"><b>${esc(f.label)}</b>${badge}</div>${input}<small>${esc(f.help)}</small></div>`;
  };
  const extra = {
    zugang: `<div class="btn-row"><button type="button" class="btn-tonal" id="stFromLogin"><span class="ms">badge</span>Aus meiner Cloudflare-Anmeldung übernehmen</button>
      <button type="button" class="btn-tonal" id="stNewTok"><span class="ms">key</span>Neuen IDA-Token erzeugen</button></div>`,
    mesh: `<div class="btn-row"><button type="button" class="btn-tonal" id="stTestCf"><span class="ms">verified</span>Token prüfen</button><span id="stCfOut" class="small"></span></div>
      <div class="hp-field" id="stSelf"><div class="hp-field-head"><b>Health selbst im Mesh</b></div><div class="muted small">Lade …</div></div>`,
    ida: `<div class="btn-row"><button type="button" class="btn-primary" id="stLink"><span class="ms">link</span>Mit IDA verbinden</button>
      <span class="small muted">Erzeugt den Token (falls nötig) und trägt Adresse + Token in IDA ein – danach hat Ida die geraet_*-Tools.</span></div>`,
  };
  const status = [
    STATUS(v.access_checked, v.access_checked ? "Cloudflare Access wird geprüft (Team + AUD gesetzt)" : "Cloudflare Access noch nicht eingetragen – unten unter „Zugang“", "shield_lock"),
    STATUS(v.secured, v.secured ? "Health ist abgesichert" : "Health ist noch nicht abgesichert – nur mit Einrichtungs-Code erreichbar"),
    STATUS(v.ida.linked, v.ida.linked ? `Mit IDA verbunden (${esc(v.ida.url)})` : v.ida.reachable ? "IDA erreichbar, aber noch nicht verbunden – unten „Mit IDA verbinden“" : "IDA nicht erreichbar unter der eingetragenen Adresse", "link"),
  ].join("");
  body.innerHTML = `<section class="card"><div class="card-head"><h3><span class="ms">fact_check</span>Stand</h3>
      ${me.admin ? `<button class="btn-tonal sm" id="stLogout"><span class="ms">logout</span>Einrichtung abmelden</button>` : `<span class="muted small">angemeldet als ${esc(me.who)}</span>`}</div>
      <div class="hp-stats">${status}</div>
      <p class="muted small">Alles hier gilt sofort und steht in der Datenbank von Health. Die <code>.env</code> braucht nur noch den Port (<code>HEALTH_PORT</code>).
      Werte, die im Browser gesetzt sind, gehen vor der .env; „zurücksetzen“ nimmt wieder den Wert aus der .env.</p></section>` +
    v.groups.map((g) => `<section class="card" data-g="${g.id}"><div class="card-head"><h3><span class="ms">${g.icon}</span>${esc(g.title)}</h3>
        <button class="btn-primary sm" data-save="${g.id}"><span class="ms">save</span>Speichern</button></div>
        <div class="hp-fields">${g.fields.map(field).join("")}</div>${extra[g.id] || ""}</section>`).join("");

  const collect = (gid) => {
    const out = {};
    body.querySelectorAll(`[data-g="${gid}"] [data-k]`).forEach((el) => {
      const k = el.dataset.k;
      if (el.type === "checkbox") out[k] = el.checked;
      else if (el.type === "password") { if (el.value) out[k] = el.value; }
      else out[k] = el.value;
    });
    return out;
  };
  const reload = () => settingsView(app, topbar, me);
  body.onclick = async (e) => {
    const save = e.target.closest("[data-save]");
    if (save) {
      const vals = collect(save.dataset.save);
      if (vals.allow_open && !(await confirmDialog("Wirklich ohne Schutz?", "Dann hat jeder, der die Adresse erreicht, Root-Zugriff auf alle Geräte. Nur zum Testen.", { ok: "Trotzdem", danger: true }))) return;
      if (await attempt(() => api.put("/settings", { values: vals }), "Gespeichert")) reload();
      return;
    }
    const rs = e.target.closest("[data-reset]");
    if (rs && await attempt(() => api.put("/settings", { reset: [rs.dataset.reset] }), "Zurückgesetzt")) reload();
  };
  $("stFromLogin").onclick = () => attempt(async () => {
    const r = await api.post("/settings/access-from-login");
    body.querySelector('[data-k="access_team"]').value = r.access_team;
    body.querySelector('[data-k="access_aud"]').value = r.access_aud;
    toast(`Übernommen (${r.email}) – jetzt Speichern`);
  });
  $("stNewTok").onclick = async () => {
    if (!(await confirmDialog("Neuer IDA-Token?", "Der alte gilt dann nicht mehr. Danach „Mit IDA verbinden“, damit IDA den neuen kennt."))) return;
    if (await attempt(() => api.post("/settings/ida-token"), "Neuer Token erzeugt")) reload();
  };
  $("stTestCf").onclick = () => attempt(async () => {
    const r = await api.post("/settings/test-cloudflare");
    $("stCfOut").innerHTML = `${r.token ? "✓ Token gültig" : "✗ Token ungültig"} · ${r.account ? `Konto ${esc(r.account.slice(0, 8))}…` : "kein Konto"} · ${r.mesh ? "✓ darf Mesh-Knoten anlegen" : `✗ kein Mesh-Recht${r.detail ? ` (${esc(r.detail)})` : ""}`}`;
  });
  // Health selbst im Mesh: Status + Beitreten/Verlassen
  const selfMesh = async () => {
    const box = $("stSelf");
    if (!box) return;
    let m;
    try { m = await api.get("/settings/mesh-self"); } catch (x) { box.innerHTML = `<div class="hint"><span class="ms">error</span>${esc(x.message)}</div>`; return; }
    const on = m.status === "verbunden";
    box.innerHTML = `<div class="hp-field-head"><b>Health selbst im Mesh</b></div>
      ${STATUS(on, on ? `Verbunden – Geräte im Mesh erreichen Health unter <b>${esc(m.address)}</b>` : m.available ? `WARP bereit, Health ist noch nicht im Mesh (${esc(m.status)})` : esc(m.reason), "hub")}
      ${!m.available && /NET_ADMIN|tun/.test(m.reason) ? `<pre class="log">  health:
    cap_add: [NET_ADMIN]
    devices: ["/dev/net/tun:/dev/net/tun"]
    sysctls: { net.ipv4.conf.all.src_valid_mark: "1" }</pre>
        <small>Das ist das Einzige, was nicht im Browser geht: einmal in die Compose-Datei, dann den Stack neu starten.</small>` : ""}
      <small>Geräte, die selbst im Mesh sind, schicken dann alles über das Mesh – sonst (oder wenn es hakt) über die öffentliche Adresse. Über die Mesh-IP
      ist nur der Geräte-Zugang offen, nicht diese Oberfläche.</small>
      ${m.available ? `<div class="btn-row"><button type="button" class="btn-primary" id="stSelfJoin"><span class="ms">hub</span>${on ? "Neu verbinden" : "Health ins Mesh aufnehmen"}</button>
        ${on || m.node ? `<button type="button" class="btn-tonal" id="stSelfLeave"><span class="ms">link_off</span>Aus dem Mesh nehmen</button>` : ""}</div>` : ""}`;
    $("stSelfJoin") && ($("stSelfJoin").onclick = (e) => attempt(async () => {
      e.currentTarget.disabled = true;
      toast("Health tritt dem Mesh bei – dauert bis zu einer Minute …");
      await api.post("/settings/mesh-self/join");
      selfMesh();
    }, "Health ist im Mesh"));
    $("stSelfLeave") && ($("stSelfLeave").onclick = async () => {
      if (!(await confirmDialog("Aus dem Mesh nehmen?", "Die Geräte verbinden sich dann wieder über die öffentliche Adresse."))) return;
      if (await attempt(() => api.post("/settings/mesh-self/leave"), "Health ist nicht mehr im Mesh")) selfMesh();
    });
  };
  selfMesh();
  $("stLink").onclick = () => attempt(async () => { await api.post("/settings/link-ida"); reload(); }, "Mit IDA verbunden");
  $("stLogout") && ($("stLogout").onclick = async () => { await api.post("/setup/logout"); location.hash = "#/"; location.reload(); });
  return {};
}
