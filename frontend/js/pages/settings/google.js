// Einstellungen → Google: einmal anmelden und alles erlauben (Aufgaben, Kontakte, Kalender, Gmail, Docs, Drive, …).
// Die Anmeldung läuft komplett über IDA: Knopf → Google fragt nach den Berechtigungen → Rückweg über IDA → wieder hier. Der Zugang bleibt auf dem Server.
import { api } from "../../api.js";
import { $, attempt, esc, toast } from "../../ui/dom.js";
import { subHead } from "./index.js";

export async function render(el) {
  const head = subHead("Google", "/einstellungen");
  const s = await api.get("/google/status").catch((e) => ({ error: e.message }));
  const back = new URLSearchParams(location.search);
  if (back.get("google")) {
    history.replaceState(null, "", location.pathname);
    toast(back.get("google") === "ok" ? "Mit Google verbunden" : `Anmeldung nicht geklappt: ${back.get("grund") || "abgebrochen"}`);
  }
  if (s.error || !s.configured) {
    el.innerHTML = head + `<div class="soon-box"><span class="ms">cloud_off</span>${s.error ? esc(s.error) : "Der Google-Dienst ist hier nicht eingerichtet."}</div>`;
    return;
  }
  const chips = (s.dienste || []).map((d) => `<span class="chip-s ${d.ok ? "on" : "off"}"><span class="ms">${d.ok ? "check_circle" : "radio_button_unchecked"}</span>${esc(d.name)}</span>`).join("");
  const btn = (label, icon = "login") => `<button class="add-btn" id="gLogin"><span class="ms">${icon}</span>${label}</button>`;
  if (s.verbunden) {
    el.innerHTML = head + `
      <div class="pcard"><div><b>${s.konto ? esc(s.konto) : "Mit Google verbunden"}</b>
        <small>${s.vollstaendig ? "Alles erlaubt" : `${s.fehlende_scopes} Berechtigung${s.fehlende_scopes === 1 ? "" : "en"} fehlen noch`}</small></div></div>
      ${s.fehler ? `<div class="ferr" style="margin:10px 0">${esc(s.fehler)}</div>` : ""}
      <div class="g-chips">${chips}</div>
      <p class="sub-intro">Damit arbeiten die Agents mit deinen Google-Diensten, und andere Teile von IDA können sie nutzen, zum Beispiel für den Kalender-Abgleich oder das Docs-Backup in Drive.${s.vollstaendig ? "" : " Melde dich noch einmal an und erlaube dort alles, dann ist es vollständig."}</p>
      <div class="es-actions">${btn(s.vollstaendig ? "Neu anmelden" : "Fehlendes erlauben", s.vollstaendig ? "refresh" : "verified_user")}
        <button class="es-btn" id="gOut"><span class="ms">logout</span>Abmelden</button></div>
      <p class="hint">Abmelden widerruft den Zugang bei Google und löscht ihn auf dem Server.</p>`;
    $("gOut").onclick = async () => { if (confirm("Von Google abmelden?") && await attempt(() => api.del("/google"), "Abgemeldet")) render(el); };
  } else {
    el.innerHTML = head + `
      <p class="sub-intro">Melde dich einmal mit deinem Google-Konto an und erlaube alles. Dann können die Agents Aufgaben, Kontakte, Kalender, Mails, Docs, Drive und mehr nutzen.</p>
      <div class="g-chips">${chips}</div>
      <div class="es-actions">${btn("Mit Google anmelden")}</div>
      <p class="hint">„Nicht verifiziert“ bei Google ist normal, das ist deine eigene App: unten auf „Erweitert“ und weiter. Alle Häkchen setzen.</p>`;
  }
  $("gLogin").onclick = async () => {
    $("gLogin").disabled = true;
    try {
      const r = await api.post("/google/login");
      location.href = r.url;
    } catch (e) { toast(e.message); $("gLogin").disabled = false; }
  };
}
