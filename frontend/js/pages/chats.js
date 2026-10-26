// "/chats" – all conversations, grouped by day, searchable (?q=).
import { api } from "../api.js";
import { setUrl } from "../router.js";
import { agentOf, colors, mainAgent } from "../store.js";
import { $, attempt, enableSwipe, esc, fmtRel } from "../ui/dom.js";
import { setAgent, setView } from "../ui/shell.js";

export async function chatsPage(_, query) {
  setAgent(mainAgent());
  setView("page");
  $("page").innerHTML = `
    <div class="page-head"><h1>Chats</h1></div>
    <a class="big-new" href="/"><span class="ms">edit_square</span>Neuer Chat</a>
    <div class="search"><span class="ms">search</span><input id="chatSearch" placeholder="Chats durchsuchen" value="${esc(query.q || "")}"></div>
    <div id="chatPageList"></div>
    <p class="hint">Zum Löschen eine Zeile zur Seite wischen.</p>`;
  let t;
  $("chatSearch").oninput = () => {
    clearTimeout(t);
    t = setTimeout(() => { const q = $("chatSearch").value.trim(); setUrl(q ? `/chats?q=${encodeURIComponent(q)}` : "/chats"); load(q); }, 250);
  };
  await load(query.q || "");
}

async function load(q) {
  const list = await api.get(`/chats?q=${encodeURIComponent(q)}`);
  const box = $("chatPageList");
  if (!box) return;
  if (!list.length) { box.innerHTML = `<div class="empty">${q ? "Nichts gefunden." : "Noch keine Chats."}</div>`; return; }
  const today = new Date().toDateString();
  const weekAgo = Date.now() - 7 * 864e5;
  const groups = [["Heute", []], ["Diese Woche", []], ["Älter", []]];
  for (const c of list) {
    const d = new Date(c.updated_at);
    groups[d.toDateString() === today ? 0 : d.getTime() > weekAgo ? 1 : 2][1].push(c);
  }
  box.innerHTML = groups.filter((g) => g[1].length).map(([name, items]) => `
    <div class="grp">${name}</div>
    <div class="clist">${items.map((c) => {
      const a = agentOf(c.agent_id), col = colors(a);
      const last = c.last ? (c.last.role === "user" ? "Du: " : a.name + ": ") + c.last.text.split("\n")[0] : "";
      return `<a class="crow" href="/chat/${c.id}" data-id="${c.id}">
        <span class="ag-dot" style="background:${col["--container"]};color:${col["--accent"]}">${esc(a.name[0])}</span>
        <span class="cmain"><span class="ctop"><b>${esc(c.title)}</b><span class="ctime">${fmtRel(c.updated_at)}</span></span>
        <small>${c.running ? `<span class="c-running"><span class="ms spin">progress_activity</span>${esc(a.name)} schreibt gerade …</span>` : esc(last)}</small></span></a>`;
    }).join("")}</div>`).join("");
  box.querySelectorAll(".crow").forEach((el) => enableSwipe(el, {
    onSwipe: async () => {
      if (!confirm("Chat löschen?")) { el.style.transform = ""; el.style.opacity = ""; return; }
      await attempt(() => api.del(`/chats/${el.dataset.id}`), "Chat gelöscht");
      load(q);
    },
  }));
}
