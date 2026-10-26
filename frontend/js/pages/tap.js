// "/t/<token>" – a tracker link (e.g. on an NFC tag): opening it ticks the tracker, with a small celebration.
// "/tracker/<id>/timer" – the same timer from the app.
// Trackers with a duration (e.g. brush teeth 3 min) first run a countdown – the display stays on meanwhile –
// and tick when it is over. The page ticks via POST once; afterwards the URL gets ?ok, so a reload or a restored
// tab only shows the result.
import { api } from "../api.js";
import { navigate, setUrl } from "../router.js";
import { mainAgent } from "../store.js";
import { $, attempt, esc } from "../ui/dom.js";
import { flashMood } from "../ui/mascot.js";
import { setAgent, setView } from "../ui/shell.js";

const BUDDY = `<div class="buddy tap-buddy" aria-hidden="true"><div class="face"><div class="eyes"><div class="eye"></div><div class="eye"></div></div></div><div class="props"></div></div>`;
const CHECK = `<svg class="tap-check" viewBox="0 0 120 120" aria-hidden="true"><circle class="tap-ring" cx="60" cy="60" r="52"/><path class="tap-tick" d="M38 62 l15 15 l30 -32"/></svg>`;
const SPARKS = Array.from({ length: 10 }, (_, i) => `<i style="--a:${i * 36}deg;--d:${(i % 3) * 60}ms"></i>`).join("");
const RING = 2 * Math.PI * 88; // circumference of the countdown ring

function frame() {
  setAgent(mainAgent());
  setView("page");
  document.body.classList.add("tap-view");
  $("page").innerHTML = `<div class="tap"><div class="tap-stage">${BUDDY}</div><div class="tap-text"><b>Einen Moment …</b></div></div>`;
}

const open = (t) => t.today.done < t.today.total;

export async function tapPage({ token }, query) {
  frame();
  const url = `/tap/${encodeURIComponent(token)}`;
  let res;
  try {
    res = await api.get(url); // only looks – ticks nothing yet
    if (query.ok) return render(res); // reloaded after the tick: no second tick
    if (res.tracker.duration_s && open(res.tracker)) {
      const done = await countdown(res.tracker, `timer:t:${token}`);
      if (!done) return;
    }
    res = await api.post(url);
  } catch (e) {
    return fail(e.message);
  }
  await setUrl(`/t/${token}?ok=1`);
  render(res);
}

export async function timerPage({ id }) {
  frame();
  try {
    const t = (await api.get("/trackers")).find((x) => x.id === id);
    if (!t) return fail("Diesen Tracker gibt es nicht");
    if (!open(t)) return render({ status: "all_done", tracker: t });
    if (t.duration_s && !(await countdown(t, `timer:id:${id}`))) return;
    const r = await api.post(`/trackers/${id}/check`, {});
    render({ ...r.ticked, tracker: r });
  } catch (e) {
    fail(e.message);
  }
}

// ---- countdown: resolves true when it ran out, false when cancelled or left ----
function countdown(t, key) {
  const slot = t.slots.find((s) => s.key === t.next);
  const total = t.duration_s * 1000;
  const saved = JSON.parse(sessionStorage.getItem(key) || "null"); // survives a reload of the page
  let end = saved?.end > Date.now() ? saved.end : null;
  let left = saved?.left ?? (end ? end - Date.now() : total);
  let paused = !!saved?.left && !saved?.end;
  if (!paused && !end) end = Date.now() + left;
  const save = () => sessionStorage.setItem(key, JSON.stringify(paused ? { left } : { end }));
  save();

  $("page").querySelector(".tap").innerHTML = `
    <div class="tmr" id="tmr">
      <svg class="tmr-ring" viewBox="0 0 200 200" aria-hidden="true"><circle class="tmr-bg" cx="100" cy="100" r="88"/>
        <circle class="tmr-fg" id="tmrFg" cx="100" cy="100" r="88" stroke-dasharray="${RING}" stroke-dashoffset="0"/></svg>
      <div class="tmr-mid"><span class="ms">${esc(t.icon || "timer")}</span><b id="tmrTime">–</b><small id="tmrState"></small></div>
    </div>
    <div class="tap-text"><b>${esc(t.name)}${slot && t.slots.length > 1 ? ` · ${esc(slot.label)}` : ""}</b>
      <small>Danach wird automatisch abgehakt</small></div>
    <div class="tap-btns">
      <button class="es-btn primary" id="tmrPause"><span class="ms">pause</span>Pause</button>
      <button class="es-btn" id="tmrCancel"><span class="ms">close</span>Abbrechen</button></div>
    <div class="tmr-note" id="tmrNote" hidden></div>`;

  // keep the display on while the timer runs
  let lock = null;
  const wake = async () => {
    if (!("wakeLock" in navigator)) return note("Dieser Browser kann das Display nicht anlassen – bitte nicht ausgehen lassen.");
    try {
      lock = await navigator.wakeLock.request("screen");
    } catch {
      note("Das Display kann gerade nicht angelassen werden (geht nur über HTTPS).");
    }
  };
  const note = (text) => { const n = $("tmrNote"); if (n) { n.hidden = false; n.innerHTML = `<span class="ms">light_mode</span>${esc(text)}`; } };
  const onVisible = () => { if (document.visibilityState === "visible" && !paused) wake(); };
  document.addEventListener("visibilitychange", onVisible);
  wake();

  return new Promise((resolve) => {
    let timer = null;
    const stop = (result) => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      lock?.release().catch(() => {});
      sessionStorage.removeItem(key);
      resolve(result);
    };
    const draw = () => {
      if (!$("tmr")) return stop(false); // left the page: no tick
      const rest = paused ? left : end - Date.now();
      if (rest <= 0) { $("tmrTime").textContent = "0:00"; done(); return stop(true); }
      const s = Math.ceil(rest / 1000);
      $("tmrTime").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      $("tmrFg").setAttribute("stroke-dashoffset", String(RING * (1 - rest / total)));
      $("tmrState").textContent = paused ? "pausiert" : "";
      $("tmr").classList.toggle("paused", paused);
    };
    $("tmrPause").onclick = () => {
      if (paused) { end = Date.now() + left; paused = false; wake(); } else { left = end - Date.now(); paused = true; lock?.release().catch(() => {}); }
      $("tmrPause").innerHTML = paused ? `<span class="ms">play_arrow</span>Weiter` : `<span class="ms">pause</span>Pause`;
      save();
      draw();
    };
    $("tmrCancel").onclick = () => { stop(false); navigate("/tracker"); };
    if (paused) $("tmrPause").innerHTML = `<span class="ms">play_arrow</span>Weiter`;
    draw();
    timer = setInterval(draw, 250);
  });
}

// time is up: a short sound (where the browser allows it) and a buzz
function done() {
  if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
  try {
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    [0, 0.25, 0.5].forEach((at, i) => {
      const o = ac.createOscillator(), g = ac.createGain();
      o.frequency.value = i === 2 ? 1175 : 880;
      g.gain.setValueAtTime(0.0001, ac.currentTime + at);
      g.gain.exponentialRampToValueAtTime(0.25, ac.currentTime + at + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + at + 0.2);
      o.connect(g).connect(ac.destination);
      o.start(ac.currentTime + at);
      o.stop(ac.currentTime + at + 0.22);
    });
    setTimeout(() => ac.close(), 1500);
  } catch { /* no sound – the buzz and the animation remain */ }
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
