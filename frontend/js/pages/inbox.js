// "/nachrichten" – notifications and answers for you; "?box=alle" shows messages between agents too.
import { api } from "../api.js";
import { navigate } from "../router.js";
import { agentOf, colors, mainAgent, setUnread } from "../store.js";
import { $, attempt, esc, fmtDateTime, md } from "../ui/dom.js";
import { setAgent, setView } from "../ui/shell.js";

const KIND = { notice: ["notifications", "Info"], task: ["assignment", "Aufgabe"], result: ["task_alt", "Ergebnis"], message: ["chat", "Nachricht"] };

export async function inboxPage(_, query) {
  setAgent(mainAgent());
  setView("page");
  const all = query.box === "alle";
  $("page").innerHTML = `
    <div class="page-head"><h1>Nachrichten</h1><span class="tag" id="unreadTag"></span></div>
    <div class="seg"><a href="/nachrichten" class="${all ? "" : "on"}">Für dich</a><a href="/nachrichten?box=alle" class="${all ? "on" : ""}">Alle</a></div>
    <button class="add-btn" id="readAll"><span class="ms">done_all</span>Alle als gelesen markieren</button>
    <div id="msgList"></div>
    <p class="hint">Agenten schicken dir hier automatisch etwas, wenn sie es für wichtig halten. Antippen öffnet den Agenten.</p>`;
  $("readAll").onclick = async () => { await attempt(() => api.post("/messages/read-all"), "Erledigt"); inboxPage(_, query); };
  const list = await api.get(`/messages?box=${all ? "all" : "user"}`);
  const unread = list.filter((m) => m.to === "user" && !m.read).length;
  $("unreadTag").textContent = unread ? `${unread} neu` : "alles gelesen";
  setUnread(unread);
  $("msgList").innerHTML = list.length ? list.map((m) => {
    const from = m.from === "user" ? { name: "Du", palette: "blau" } : agentOf(m.from);
    const to = m.to === "user" ? "dich" : agentOf(m.to).name;
    const c = colors(from);
    const [ic, label] = KIND[m.kind] || KIND.message;
    return `<div class="mcard ${m.to === "user" && !m.read ? "unread" : ""} ${m.priority === "high" ? "high" : ""}" data-id="${m.id}" data-from="${m.from}">
      <span class="ric" style="background:${c["--container"]};color:${c["--accent"]}"><span class="ms">${ic}</span></span>
      <div class="rmain"><div class="mtop"><b>${esc(m.title || label)}</b><span>${fmtDateTime(m.at)}</span></div>
        <div class="mfrom">${esc(from.name)} → ${esc(to)}${m.priority === "high" ? " · wichtig" : ""}</div>
        <div class="mtext md">${md(m.text)}</div></div></div>`;
  }).join("") : `<div class="empty">Keine Nachrichten.</div>`;
  $("msgList").onclick = async (e) => {
    const card = e.target.closest(".mcard");
    if (!card || e.target.closest("a")) return;
    await api.post(`/messages/${card.dataset.id}/read`);
    const a = agentOf(card.dataset.from);
    navigate(a.is_main || card.dataset.from === "user" ? "/" : `/a/${a.id}`);
  };
}
