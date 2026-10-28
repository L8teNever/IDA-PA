// Einstellungen → Code-Bereich: switch it on/off (it gives Ida real file and shell tools in your projects).
import { api } from "../../api.js";
import { loadBoot } from "../../store.js";
import { attempt, esc, sw } from "../../ui/dom.js";
import { subHead } from "./index.js";

export async function render(el) {
  const s = await api.get("/code/settings");
  el.innerHTML = subHead("Code-Bereich", "/einstellungen") + `
    <p class="sub-intro">Im <b>Code-Bereich</b> arbeitet Ida direkt in deinen Projekten (GitHub-Repos): pro Projekt mehrere Threads, in denen sie Code liest, Dateien ändert und Befehle ausführt – wie in T3 Code, nur in IDA. Sie nutzt dabei dasselbe Gedächtnis wie überall, und in normalen Chats findet sie bei Bedarf, was ihr in den Threads gemacht habt.</p>
    <div class="slist"><div class="srow"><span class="smain"><b>Code-Bereich</b><small>${s.enabled ? "An – „Code“ steht im Menü" : "Aus"}</small></span>${sw(s.enabled, 'id="codeSw"')}</div></div>
    <div class="rf-sum warn-box" style="margin-top:14px"><span class="ms">shield</span><span><b>Wichtig:</b> Code-Threads haben echten Zugriff auf Dateien und die Shell des Servers (Projekte liegen unter <span class="mono">data/projects</span>). IDA hat keine eigene Anmeldung – schalte das nur ein, wenn IDA geschützt ist (z. B. Cloudflare Zero Trust oder VPN).</span></div>
    <div class="pcard help" style="margin-top:14px"><b>Rechte pro Thread</b>
      <ol><li><b>Nachfragen</b> (Standard): Jede Dateiänderung und jeder Befehl erscheint im Thread und wartet auf dein OK – so lange, bis du antwortest.</li>
      <li><b>Edits automatisch</b>: Dateien ändern ohne Nachfrage, Befehle fragen nach.</li>
      <li><b>Voller Zugriff</b>: alles ohne Nachfrage.</li></ol></div>
    <div class="rf-sum" style="margin-top:10px"><span class="ms">hub</span><span>Für deine Repos, Push und Pull Requests: <a href="/einstellungen/github">mit GitHub anmelden</a>${s.github?.connected ? ` (angemeldet als <b>${esc(s.github.login)}</b>)` : ""}.</span></div>`;
  el.querySelector("#codeSw").onclick = async () => {
    const on = !s.enabled;
    if (on && !confirm("Code-Bereich einschalten? Ida bekommt dort echten Zugriff auf Dateien und Shell des Servers.")) return;
    if (await attempt(() => api.put("/code/settings", { enabled: on }), on ? "Code-Bereich an" : "Code-Bereich aus")) {
      await loadBoot();
      render(el);
    }
  };
}
