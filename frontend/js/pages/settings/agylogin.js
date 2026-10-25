// Einstellungen → Accounts → /einstellungen/accounts/:id for Antigravity – sign-in like in T3 Code:
// (first time: the official Antigravity runtime is downloaded on the server), then Google link → sign in →
// the browser lands on http://127.0.0.1:… (does not load, that is fine) → paste that address here → done.
import { api } from "../../api.js";
import { loadBoot } from "../../store.js";
import { $, esc, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

let poll = null;
const mb = (b) => `${Math.round((b || 0) / 1048576)} MB`;

export async function agyLoginView(el, a) {
  clearInterval(poll);
  const draw = (inner) => {
    el.innerHTML = subHead(`${a.name} – Antigravity`, "/einstellungen/accounts") + `<div class="agy-login">${inner}</div>
      <p class="sub-intro" style="margin-top:14px">⚠ Google hat Konten gesperrt, deren Antigravity-Login in fremden Programmen genutzt wurde. IDA nutzt – wie T3 Code – Googles offiziellen Antigravity-Agenten, eine ausdrückliche Freigabe gibt es aber nicht. Nimm am besten ein Zweitkonto.</p>`;
  };

  const showDone = async () => {
    const st = (await api.get("/accounts")).find((x) => x.id === a.id);
    const boot = await loadBoot();
    const models = boot.models.filter((m) => m.startsWith("agy:"));
    draw(`<div class="pcard"><div class="term-status">${dot(true)} angemeldet${st?.logged_in ? "" : " (?)"}</div>
      <p class="sub-intro" style="margin:8px 0">Modelle dieses Kontos (im Modell-Menü beim Account auswählbar):</p>
      <div class="chipset">${models.map((m) => `<span class="chip-static">${esc(boot.model_names?.[m] || m.slice(4))}</span>`).join("") || "<span class='sub-intro'>noch keine – „Modelle neu laden“</span>"}</div>
      <div class="btn-row" style="margin-top:14px"><button class="es-btn" id="gRefresh"><span class="ms">refresh</span>Modelle neu laden</button>
      <button class="es-btn" id="gRelogin"><span class="ms">login</span>Neu anmelden</button>
      <a class="es-btn" href="/limits"><span class="ms">data_usage</span>Limits</a></div></div>`);
    $("gRefresh").onclick = async () => {
      $("gRefresh").disabled = true;
      try { await api.post(`/accounts/${a.id}/refresh`); toast("Modelle geladen"); } catch (e) { toast(e.message); }
      showDone();
    };
    $("gRelogin").onclick = () => start(true);
  };

  const showInstall = (inst) => {
    const pct = inst.total ? Math.round((inst.done / inst.total) * 100) : 0;
    draw(`<div class="pcard"><b>Antigravity wird eingerichtet …</b>
      <p class="sub-intro" style="margin:6px 0 10px">Einmalig lädt der Server Googles offiziellen Antigravity-Agenten (Version ${esc(inst.version)}, ca. 330 MB, entpackt gut 1 GB).</p>
      <div class="ubar big"><div style="width:${inst.phase === "extracting" ? 100 : pct}%"></div></div>
      <div class="pc-foot">${inst.phase === "error" ? "⚠ " + esc(inst.error || "Fehler") : inst.phase === "extracting" ? "wird entpackt …" : `${mb(inst.done)} von ${mb(inst.total)}`}</div>
      ${inst.phase === "error" ? `<button class="es-btn primary" id="gRetry" style="margin-top:10px"><span class="ms">refresh</span>Nochmal</button>` : ""}</div>`);
    if ($("gRetry")) $("gRetry").onclick = () => { api.post("/antigravity/runtime"); start(true); };
  };

  const showWaiting = (url) => {
    draw(`<div class="pcard agy-steps">
      <div class="agy-step"><span class="agy-n">1</span><div><b>Bei Google anmelden</b>
        <p class="sub-intro">Öffnet Google in einem neuen Tab. Mit dem Antigravity-Konto anmelden und zustimmen.</p>
        <a class="es-btn primary" href="${esc(url)}" target="_blank" rel="noopener"><span class="ms">open_in_new</span>Google-Anmeldung öffnen</a></div></div>
      <div class="agy-step"><span class="agy-n">2</span><div><b>Rückkehr-Adresse einfügen</b>
        <p class="sub-intro">Danach landet der Browser auf einer Seite, die <b>nicht lädt</b> (http://127.0.0.1:…) – das ist richtig so. Kopiere die <b>komplette Adresse</b> aus der Adresszeile und füge sie hier ein.</p>
        <div class="agy-paste"><input id="gUrl" class="mini-in" placeholder="http://127.0.0.1:…/?state=…&code=…" autocomplete="off" spellcheck="false">
          <button class="es-btn" id="gPaste" title="Aus der Zwischenablage"><span class="ms">content_paste</span></button>
          <button class="es-btn primary" id="gGo"><span class="ms">check</span>Weiter</button></div>
        <div class="pc-foot" id="gMsg"></div></div></div></div>`);
    $("gPaste").onclick = async () => { try { $("gUrl").value = (await navigator.clipboard.readText()).trim(); } catch { toast("Bitte selbst einfügen (Strg+V)"); } };
    const go = async () => {
      const v = $("gUrl").value.trim();
      if (!v) return $("gUrl").focus();
      $("gGo").disabled = true;
      $("gMsg").textContent = "wird übergeben …";
      try {
        await api.post(`/accounts/${a.id}/antigravity/callback`, { url: v });
        toast(`${a.name} ist angemeldet`);
        showDone();
      } catch (e) {
        $("gMsg").textContent = "⚠ " + e.message;
        $("gGo").disabled = false;
      }
    };
    $("gGo").onclick = go;
    $("gUrl").onkeydown = (e) => { if (e.key === "Enter") go(); };
  };

  async function start(force) {
    clearInterval(poll);
    draw(`<div class="pcard"><div class="term-status">${dot(false)} startet Antigravity …</div></div>`);
    try {
      const st = await api.post(`/accounts/${a.id}/antigravity/login`);
      if (st.phase === "installing") {
        showInstall(st.install);
        poll = setInterval(async () => {
          if (!document.body.contains(el)) return clearInterval(poll);
          const inst = await api.get("/antigravity/runtime");
          if (inst.installed) { clearInterval(poll); start(true); } else showInstall(inst);
        }, 1500);
      } else if (st.phase === "waiting") showWaiting(st.url);
      else showDone();
    } catch (e) {
      draw(`<div class="pcard"><div class="term-status">${dot(false)} ${esc(e.message)}</div>
        <button class="es-btn primary" id="gRetry" style="margin-top:10px"><span class="ms">refresh</span>Nochmal</button></div>`);
      $("gRetry").onclick = () => start(true);
    }
    void force;
  }

  if (a.logged_in) showDone(); else start(false);
}

const dot = (ok) => `<span class="sdot" style="background:${ok ? "#7ee2b8" : "#ffc85a"}"></span>`;
