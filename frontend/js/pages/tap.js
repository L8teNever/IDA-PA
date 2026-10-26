// "/t/<token>" – a tracker link (e.g. on an NFC tag): opening it ticks the tracker, with a small celebration.
// The page ticks via POST once; afterwards the URL gets ?ok, so a reload or a restored tab only shows the result.
import { api } from "../api.js";
import { navigate, setUrl } from "../router.js";
import { mainAgent } from "../store.js";
import { $, attempt, esc } from "../ui/dom.js";
import { flashMood } from "../ui/mascot.js";
import { setAgent, setView } from "../ui/shell.js";

const BUDDY = `<div class="buddy tap-buddy" aria-hidden="true"><div class="face"><div class="eyes"><div class="eye"></div><div class="eye"></div></div></div><div class="props"></div></div>`;
const CHECK = `<svg class="tap-check" viewBox="0 0 120 120" aria-hidden="true"><circle class="tap-ring" cx="60" cy="60" r="52"/><path class="tap-tick" d="M38 62 l15 15 l30 -32"/></svg>`;
const SPARKS = Array.from({ length: 10 }, (_, i) => `<i style="--a:${i * 36}deg;--d:${(i % 3) * 60}ms"></i>`).join("");

export async function tapPage({ token }, query) {
  setAgent(mainAgent());
  setView("page");
  document.body.classList.add("tap-view");
  $("page").innerHTML = `<div class="tap"><div class="tap-stage">${BUDDY}</div><div class="tap-text"><b>Einen Moment …</b></div></div>`;
  let res;
  try {
    // reloaded after the tick (?ok): only show the state, no second tick
    res = query.ok ? await api.get(`/tap/${encodeURIComponent(token)}`) : await api.post(`/tap/${encodeURIComponent(token)}`);
  } catch (e) {
    return fail(e.message);
  }
  if (!query.ok) await setUrl(`/t/${token}?ok=1`);
  render(res);
}

function fail(msg) {
  flashMood("sad", 8000);
  $("page").querySelector(".tap").innerHTML = `<div class="tap-stage">${BUDDY}</div>
    <div class="tap-text"><b>Das hat nicht geklappt</b><small>${esc(msg || "Link unbekannt")}</small></div>
    <div class="tap-btns"><a class="es-btn" href="/tracker"><span class="ms">checklist</span>Zum Tracker</a><a class="es-btn" href="/"><span class="ms">home</span>Startseite</a></div>`;
}

function render(res) {
  const t = res.tracker;
  const ok = res.status === "checked";
  const title = {
    checked: t ? `${esc(t.name)}${t.slots.length > 1 ? ` · ${esc(res.slot.label)}` : ""} abgehakt` : "",
    all_done: t ? `${esc(t.name)}: heute schon alles erledigt 🎉` : "",
    already: t ? `${esc(res.slot.label)} war schon abgehakt` : "",
    repeat: "Gerade eben schon abgehakt",
    shown: t ? `${esc(t.name)}: ${t.today.done} von ${t.today.total} erledigt` : "",
  }[res.status];
  flashMood(ok ? "happy" : res.status === "all_done" ? "love" : "idle", 8000);
  if (ok && navigator.vibrate) navigator.vibrate([30, 60, 30]);
  const today = t?.today;
  $("page").querySelector(".tap").innerHTML = `
    <div class="tap-stage ${ok ? "go" : "calm"}">${BUDDY}<div class="tap-badge">${CHECK}<div class="tap-sparks">${ok ? SPARKS : ""}</div></div></div>
    <div class="tap-text"><b>${title}</b>
      ${t?.streak ? `<small>🔥 ${t.streak} ${t.streak === 1 ? "Tag" : "Tage"} am Stück</small>` : ""}</div>
    ${today ? `<div class="tap-slots">${today.slots.map((s) => `<span class="${s.done ? "on" : ""}"><span class="ms">${s.done ? "check_circle" : "radio_button_unchecked"}</span>${esc(s.label)}${s.time ? " " + s.time : ""}</span>`).join("")}</div>` : ""}
    <div class="tap-btns">
      ${ok ? `<button class="es-btn" id="tapUndo"><span class="ms">undo</span>Rückgängig</button>` : ""}
      <a class="es-btn primary" href="/tracker"><span class="ms">checklist</span>Zum Tracker</a>
      <a class="es-btn" href="/"><span class="ms">home</span>Startseite</a></div>`;
  if ($("tapUndo")) $("tapUndo").onclick = async () => {
    if (await attempt(() => api.post(`/trackers/${t.id}/check`, { slot: res.slot.key, day: res.day, undo: true }), "Zurückgenommen")) navigate("/tracker");
  };
}
