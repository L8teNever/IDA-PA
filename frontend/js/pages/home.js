// "/" – start page of the main agent (infos = unread notifications, today = calendar events + marked routines/reminders) and "/a/:id".
import { api } from "../api.js";
import { navigate } from "../router.js";
import { agentById, agentOf, colors, kalenderUrl, mainAgent, setUnread } from "../store.js";
import { $, attempt, enableSwipe, esc, fmtTime } from "../ui/dom.js";
import { setAgent, setView } from "../ui/shell.js";
import { newChat, send } from "./chat.js";

let pastOpen = false;

export async function homePage() {
  const a = mainAgent();
  setAgent(a);
  newChat(a);
  setView(a.home === "timeline" ? "home" : "agent");
  if (a.home !== "timeline") return renderAgentHome(a);
  $("dayName").textContent = new Date().toLocaleDateString("de-DE", { weekday: "long" });
  $("daySub").innerHTML = `<span class="ms">calendar_today</span>${new Date().toLocaleDateString("de-DE", { day: "numeric", month: "long" })}`;
  await refresh();
}

export function agentPage({ id }) {
  const a = agentById(id);
  if (!a) return navigate("/", { replace: true });
  if (a.is_main) return navigate("/", { replace: true });
  setAgent(a);
  newChat(a);
  setView(a.home === "timeline" ? "home" : "agent");
  if (a.home === "timeline") return refresh();
  renderAgentHome(a);
}

function renderAgentHome(a) {
  $("ahName").textContent = a.name;
  $("ahDesc").textContent = a.intro || a.description;
  $("ahChips").innerHTML = (a.chips || []).map(([ic, t]) => `<button data-t="${esc(t)}"><span class="ms">${esc(ic)}</span>${esc(t)}</button>`).join("")
    + `<button data-go="/einstellungen/agents/${a.id}"><span class="ms">tune</span>Einstellungen</button>`;
  $("ahChips").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.go) return navigate(b.dataset.go);
    send(b.dataset.t);
  };
}

let calendar = { on: false, error: null };

async function refresh() {
  const data = await api.get("/home");
  calendar = data.calendar || calendar;
  renderNotes(data.notices);
  renderTimeline(data.timeline);
}

function renderNotes(notes) {
  const box = $("notes");
  document.body.classList.toggle("alert", notes.length > 0);
  $("bellCount").textContent = notes.length;
  setUnread(notes.length);
  if (!notes.length) { box.hidden = true; document.body.classList.remove("show-bell"); return; }
  box.hidden = false;
  const fa = colors(agentOf(notes[0].from));
  const rs = document.documentElement.style;
  rs.setProperty("--alert", fa["--accent"]);
  rs.setProperty("--alert-rgb", fa["--accent-rgb"]);
  rs.setProperty("--alert-container", fa["--container"]);
  rs.setProperty("--on-alert-container", fa["--on-container"]);
  rs.setProperty("--on-alert", fa["--on-accent"]);
  const icon = (n) => (n.kind === "notice" ? (n.priority === "high" ? "priority_high" : "notifications") : n.kind === "result" ? "task_alt" : "chat");
  const item = (n, i, cls) => {
    const a = agentOf(n.from), c = colors(a);
    const [title, ...rest] = n.title ? [n.title, n.text] : [n.text.split("\n")[0].slice(0, 60), n.text.slice(60)];
    return `<button class="note ${cls}" data-i="${i}" style="--n-acc:${c["--accent"]};--n-rgb:${c["--accent-rgb"]}">
      <span class="ic"><span class="ms">${icon(n)}</span></span>
      <span class="ntxt"><b>${esc(title)}</b><small>${esc(rest.join(" ").slice(0, 90) || fmtTime(n.at))}</small></span>
      <span class="who">${esc(a.name)}</span></button>`;
  };
  const [first, ...rest] = notes;
  box.innerHTML = `<div class="note-row">${item(first, 0, "first" + (rest.length ? "" : " only"))}
      ${rest.length ? `<button class="more-btn" id="moreBtn" aria-label="Weitere Infos"><span>+${rest.length}</span><span class="ms chev">expand_more</span></button>` : ""}</div>
    ${rest.map((n, i) => item(n, i + 1, "extra" + (i === rest.length - 1 ? " last" : ""))).join("")}`;
  if (rest.length) $("moreBtn").onclick = () => box.classList.toggle("expanded");
  box.querySelectorAll(".note").forEach((el) => {
    const n = notes[+el.dataset.i];
    enableSwipe(el, {
      onSwipe: async () => { await api.post(`/messages/${n.id}/read`); refresh(); },
      onTap: async () => {
        await api.post(`/messages/${n.id}/read`);
        const a = agentOf(n.from);
        navigate(a.is_main ? "/nachrichten" : `/a/${a.id}`);
      },
    });
  });
}

function renderTimeline(all) {
  const now = Date.now();
  const allDay = all.filter((x) => x.all_day);
  const items = all.filter((x) => !x.all_day);
  let nowIdx = items.findIndex((x) => new Date(x.end || x.at).getTime() >= now - 15 * 60000);
  if (nowIdx < 0) nowIdx = items.length;
  const html = (x, i) => {
    if (x.kind === "event") {
      const span = x.all_day ? "" : x.end ? `${fmtTime(x.at)}–${fmtTime(x.end)}` : fmtTime(x.at);
      const sub = [x.all_day ? "ganztägig" : span, x.location].filter(Boolean).join(" · ");
      const own = x.event_id && !x.holiday && kalenderUrl(); // Termin aus dem IDA-Kalender: antippen öffnet ihn in der Kalender-App
      return `<${own ? "a" : "div"} class="ev event ${i < nowIdx ? "past" : i === nowIdx ? "now" : ""}" ${own ? `href="${esc(kalenderUrl())}/termin/${esc(x.event_id)}" target="_blank" rel="noopener"` : ""} ${x.color ? `data-color style="--evc:var(--ev-${esc(x.color)})"` : ""}>
        <span class="time">${x.all_day ? `<span class="ms">${x.holiday ? "celebration" : "event"}</span>` : fmtTime(x.at)}</span>
        <span class="evtxt"><b>${esc(x.title)}</b><small>${esc(sub)}</small></span>${own ? `<span class="ms evchev">open_in_new</span>` : ""}</${own ? "a" : "div"}>`;
    }
    if (x.kind === "tracker") {
      const by = x.by === "link" ? " · per NFC" : x.by && x.by !== "user" ? ` · von ${agentOf(x.by).name}` : "";
      return `<div class="ev trk-ev ${x.done ? "done" : ""} ${i < nowIdx ? "past" : i === nowIdx ? "now" : ""}" data-href="/tracker/${esc(x.tracker_id)}">
        <span class="time">${x.all_day ? `<span class="ms">${esc(x.icon || "task_alt")}</span>` : fmtTime(x.at)}</span>
        <span class="evtxt"><b>${esc(x.title)}</b><small>${x.done ? "abgehakt" + esc(by) : "offen"}</small></span>
        <button type="button" class="trk-tick" data-tick="${esc(x.tracker_id)}" data-slot="${esc(x.slot)}" data-done="${x.done ? 1 : ""}" data-dur="${x.duration_s || ""}"
          aria-label="${x.done ? "Zurücknehmen" : "Abhaken"}"><span class="ms">${x.done ? "check" : ""}</span></button></div>`;
    }
    const a = agentOf(x.agent_id);
    const sub = x.kind === "reminder" ? (x.status === "pending" ? (x.task ? `${a.name} erledigt es` : "Erinnerung") : x.status === "done" ? "erledigt" : "fehlgeschlagen")
      : x.kind === "next" ? `geplant · ${a.name}` : `${x.ok ? "erledigt" : "fehlgeschlagen"} · ${a.name}`;
    return `<button class="ev ${i < nowIdx ? "past" : i === nowIdx ? "now" : ""}" data-href="${x.kind === "reminder" ? "/erinnerungen/" + x.reminder_id : "/routinen/" + x.routine_id}">
      <span class="time">${fmtTime(x.at)}</span>
      <span class="evtxt"><b>${esc(x.title)}</b><small>${esc(sub)}</small></span>
      <span class="ms evchev">chevron_right</span></button>`;
  };
  const pastN = Math.min(nowIdx, items.length);
  const calHint = calendar.error
    ? `<a class="cal-hint" href="/einstellungen/startseite"><span class="ms">event_busy</span>Kalender nicht erreichbar: ${esc(calendar.error)}</a>`
    : calendar.on ? "" : `<a class="cal-hint" href="/einstellungen/startseite"><span class="ms">calendar_add_on</span>Kalender verbinden, damit deine Termine hier stehen</a>`;
  $("pastSlot").innerHTML = pastN ? `<button class="past-toggle ${pastOpen ? "open" : ""}" id="pastToggle"><span class="ms">history</span>${pastN}<span class="ms pchev">expand_more</span></button>` : "";
  $("timeline").innerHTML = (all.length ? "" : `<div class="empty-day"><span class="ms">wb_sunny</span>Heute stehen keine Termine an.</div>`)
    + allDay.map((x) => html(x, Infinity)).join("")
    + (pastN ? `<div class="past-wrap ${pastOpen ? "open" : ""}"><div>${items.slice(0, pastN).map(html).join("")}</div></div>` : "")
    + items.slice(pastN).map((x, k) => html(x, k + pastN)).join("")
    + calHint;
  $("timeline").onclick = async (e) => {
    const b = e.target.closest("[data-tick]");
    if (!b) return;
    e.preventDefault(); // not the row's link to /tracker
    const undo = !!b.dataset.done;
    if (!undo && b.dataset.dur) return navigate(`/tracker/${b.dataset.tick}/timer`); // with a duration: the timer first
    b.disabled = true;
    if (await attempt(() => api.post(`/trackers/${b.dataset.tick}/check`, { slot: b.dataset.slot, undo }), undo ? "Zurückgenommen" : "✓ Abgehakt")) refresh();
    else b.disabled = false;
  };
  $("pastSlot").onclick = (e) => { if (e.target.closest("#pastToggle")) { pastOpen = !pastOpen; renderTimeline(all); } };
}

// Scroll: mascot and bell move into the header
export function initHomeScroll() {
  const home = $("home");
  const onScroll = () => {
    const y = home.scrollTop;
    const hero = home.querySelector(".hero");
    const notes = $("notes");
    document.body.classList.toggle("show-mini", y > hero.offsetTop + hero.offsetHeight * 0.6);
    document.body.classList.toggle("show-bell", !notes.hidden && y > notes.offsetTop + notes.offsetHeight - 16);
  };
  home.addEventListener("scroll", onScroll, { passive: true });
  $("hdrMini").onclick = () => home.scrollTo({ top: 0, behavior: "smooth" });
  $("hdrBell").onclick = () => { $("notes").classList.add("expanded"); home.scrollTo({ top: 0, behavior: "smooth" }); };
  setInterval(() => { if (!document.hidden && !$("home").hidden && !document.body.classList.contains("chatting")) refresh().catch(() => {}); }, 60000);
}
