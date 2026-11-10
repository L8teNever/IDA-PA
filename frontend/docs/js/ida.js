// Chat mit Ida in Docs. Sie bekommt die Auswahl, die Seite oder den Tab mitgeschickt und antwortet auf Änderungswünsche mit einem Vorschlag,
// den der Nutzer per Knopf übernimmt (Strg+Z macht es rückgängig). Der Text landet erst nach dem Klick im Dokument.
// Was nur für Ida bestimmt ist, steht hinter MARK: Im Panel sieht man nur die eigentliche Frage. Die Chats erscheinen auch in der IDA-App.
import { render } from "./markdown.js";

const MARK = "\n\n---IDA-DOCS-KONTEXT---\n";
const OPEN = "<<<IDA-VORSCHLAG", CLOSE = ">>>IDA-ENDE";
const KINDS = { sel: "die Auswahl", page: "die Seite", doc: "den ganzen Tab" };

const RULES = `Du hilfst gerade in IDA Docs, dem Editor für Aufsätze und Projektarbeiten. Auf das Dokument hast du keinen Zugriff außer über diese Nachricht.
Fragen beantwortest du normal und kurz. Soll Text geändert oder neu geschrieben werden, gib genau einen Vorschlag in diesem Format aus, den Simon per Knopf übernimmt:
${OPEN}
neuer Text
${CLOSE}
Der Vorschlag ersetzt den Abschnitt BEREICH vollständig, gib also den ganzen neuen Inhalt dieses Bereichs aus, keine Auslassungen. Außerhalb des Blocks höchstens ein bis zwei Sätze dazu.
Halte die Markdown-Syntax des Dokuments ein und lass Vorhandenes stehen, das nicht geändert werden soll: \\newpage (Seitenumbruch), [TOC], [QUELLEN], ::: title|center|right|left|entwurf … :::, ##Kommentar, [[Notiz: …]], [@schluessel, Seite], [^1]. Erfinde keine Quellen.`;

export function createIda({ st, $, el, esc, ico, toast, api, switchTab }) {
  const ai = { docId: null, chatId: null, msgs: [], sel: null, scope: "doc", running: false, ctrl: null, models: null, model: localStorage.getItem("docs.ai.model") || "", lastFull: "", lastOthers: "", status: "", applied: new Set(), loaded: false };
  let timer = 0;
  const ta = () => $("#src"), open = () => $(".split")?.classList.contains("aion"), mobile = () => matchMedia("(max-width: 900px)").matches;

  // ---------------------------------------------------------------- Kontext für Ida
  function pageRange() {
    const v = ta().value, at = ta().selectionStart, starts = Object.entries(st.pageStarts || {}).map(([p, o]) => [+p, o]).sort((a, b) => a[1] - b[1]);
    let from = 0, to = v.length, page = 1;
    for (const [p, o] of starts) { if (o <= at) { from = o; page = p; } else { to = o; break; } }
    to -= v.slice(from, to).match(/\s*(\\(newpage|pagebreak)\s*)?$/i)[0].length;   // der Umbruch zur nächsten Seite gehört nicht zur Seite: Sonst ginge er beim Ersetzen verloren
    return { from, to: Math.max(from, to), page };
  }
  function target() {
    const v = ta().value;
    if (ai.sel) return { kind: "sel", from: ai.sel.from, text: ai.sel.text, label: "Markierte Stelle" };
    if (ai.scope === "page") { const r = pageRange(); return { kind: "page", from: r.from, text: v.slice(r.from, r.to), label: `Seite ${r.page}` }; }
    return { kind: "doc", from: 0, text: v, label: ai.scope === "all" ? "Ganzes Dokument" : "Ganzer Tab" };
  }
  function context() {
    const d = st.doc, tab = d.tabs[d.tab], v = ta().value, t = target(), full = `${tab.id}:${v}`, same = ai.lastFull === full;
    const area = t.kind === "doc" && same ? "BEREICH: unverändert seit deiner letzten Nachricht zu diesem Tab" : `<<<BEREICH\n${t.text}\n>>>BEREICH`;
    const rest = t.kind !== "doc" && !same ? `\n\nGesamter Text des Tabs, nur zum Verstehen:\n<<<GESAMT\n${v}\n>>>GESAMT` : "";
    let others = "", othersKey = "";
    if (ai.scope === "all" && d.tabs.length > 1) {
      const list = d.tabs.map((x, n) => [x, n]).filter(([, n]) => n !== d.tab).map(([x]) => x);
      othersKey = list.map((x) => `${x.id}:${x.content}`).join("\u0001");
      others = ai.lastOthers === othersKey ? "\n\nWeitere Tabs des Dokuments: unverändert seit deiner letzten Nachricht. Sie sind nur zum Verstehen, geändert wird nur der Bereich oben."
        : `\n\nWeitere Tabs des Dokuments, nur zum Verstehen (geändert wird nur der Bereich oben):\n${list.map((x) => `<<<TAB ${x.title}\n${x.content}\n>>>TAB`).join("\n")}`;
    }
    return { full, othersKey, text: `${RULES}\n\nDokument: ${d.title}\nTab: ${tab.title}\nTab-ID: ${tab.id}\nArt: ${t.kind}\nPosition: ${t.from}\nBereich: ${t.label}\n${area}${rest}${others}` };
  }
  // Wozu gehört ein Vorschlag? Steht in der Nachricht davor (bei "unverändert" in einer früheren).
  function originOf(i) {
    let info = null;
    for (let j = i - 1; j >= 0; j--) {
      const c = ai.msgs[j].role === "user" && ai.msgs[j].text.split(MARK)[1]; if (!c) continue;
      const tabId = c.match(/^Tab-ID: (.+)$/m)?.[1], kind = c.match(/^Art: (\w+)$/m)?.[1], pos = +(c.match(/^Position: (\d+)$/m)?.[1] || 0);
      info ||= { tabId, kind, pos };
      if (tabId !== info.tabId) continue;
      // "unverändert" kann sich auch auf den Gesamttext einer früheren Nachricht zu einer Auswahl beziehen
      const m = info.kind === "doc" && kind !== "doc" ? c.match(/<<<GESAMT\n([\s\S]*?)\n>>>GESAMT/) : kind === info.kind ? c.match(/<<<BEREICH\n([\s\S]*?)\n>>>BEREICH/) : null;
      if (m) return { ...info, orig: m[1] };
    }
    return info;
  }
  function apply(i, text) {
    const o = originOf(i), d = st.doc;
    const n = o ? d.tabs.findIndex((t) => t.id === o.tabId) : -1;
    if (n < 0 || o.orig == null) { toast("Zu diesem Vorschlag finde ich die Stelle nicht mehr"); return false; }
    if (n !== d.tab) switchTab(n);
    const t = ta(), at = t.value.substr(o.pos, o.orig.length) === o.orig ? o.pos : t.value.indexOf(o.orig);
    if (at < 0) { toast("Die Stelle wurde inzwischen geändert. Frag Ida noch einmal."); return false; }
    t.focus(); t.setRangeText(text, at, at + o.orig.length, "select"); t.dispatchEvent(new Event("input"));
    ai.sel = null; drawScope();
    return true;
  }

  // ---------------------------------------------------------------- Anzeige
  function parts(text) {
    const out = []; let rest = text;
    for (let at; (at = rest.indexOf(OPEN)) >= 0;) {
      if (at) out.push({ md: rest.slice(0, at) });
      rest = rest.slice(at + OPEN.length).replace(/^\n/, "");
      const end = rest.indexOf(CLOSE);
      if (end < 0) { out.push({ prop: rest, open: true }); return out; }
      out.push({ prop: rest.slice(0, end).replace(/\n$/, "") }); rest = rest.slice(end + CLOSE.length);
    }
    if (rest.trim()) out.push({ md: rest });
    return out;
  }
  const md = (t) => render(t, { numbers: false }).html;
  function msgHtml(m, i) {
    if (m.role === "user") { const area = m.text.split(MARK)[1]?.match(/^Bereich: (.+)$/m)?.[1]; return `<div class="aimsg u">${esc(m.text.split(MARK)[0])}${area ? `<small>${esc(area)}</small>` : ""}</div>`; }
    const kind = originOf(i)?.kind;
    const body = parts(m.text).map((p, k) => p.md != null ? `<div class="aidoc">${md(p.md)}</div>` : `<div class="aiprop"><header>${ico("edit_note")}Vorschlag${kind ? ` für ${KINDS[kind] || "den Text"}` : ""}</header><pre>${esc(p.prop)}</pre>${p.open ? "" : ai.applied.has(`${i}:${k}`) ? `<footer><span>${ico("check")}Übernommen</span><small>Strg+Z macht es rückgängig</small></footer>` : `<footer><button class="btn sm pri" data-apply="${i}:${k}">Übernehmen</button></footer>`}</div>`).join("");
    return `<div class="aimsg a">${body}${m.error ? `<p class="warn">${esc(m.error)}</p>` : ""}</div>`;
  }
  function drawMsgs() {
    const box = $("#aimsgs"); if (!box) return;
    const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    box.innerHTML = (ai.msgs.length ? ai.msgs.map(msgHtml).join("") : `<div class="aiempty">${ico("auto_awesome")}<b>Frag Ida zu deinem Text</b>Markier eine Stelle oder wähl unten die Seite, den Tab oder das ganze Dokument. Sie kann erklären, umschreiben oder ganze Teile neu aufbauen. Änderungen landen erst im Text, wenn du sie übernimmst.</div>`) + (ai.running ? `<div class="aistat"><i></i>${esc(ai.status || "Ida schreibt …")}</div>` : "");
    if (stick) box.scrollTop = box.scrollHeight;
  }
  const later = () => { if (!timer) timer = setTimeout(() => { timer = 0; drawMsgs(); }, 90); };
  function drawScope() {
    const box = $("#aiscope"); if (!box) return;
    const preview = ai.sel && ai.sel.text.replace(/\s+/g, " ").trim();
    box.innerHTML = ai.sel ? `<span class="aichip on">${ico("ink_highlighter")}<span>„${esc(preview.length > 42 ? preview.slice(0, 42) + " …" : preview)}“</span><button data-x aria-label="Auswahl entfernen">${ico("close")}</button></span>`
      : [["page", "Aktuelle Seite"], ["doc", "Ganzer Tab"], ["all", "Ganzes Dokument"]].map(([k, t]) => `<button class="aichip ${ai.scope === k ? "on" : ""}" data-s="${k}">${t}</button>`).join("");
    const pill = $("#aipill"); if (pill) pill.hidden = !ai.sel || (open() && !mobile()) || st.mpane === "ai";
  }
  const modelName = (id) => id ? ai.models?.model_names?.[id] || id : "Standard";
  function drawInput() {
    const b = $("#aisend"); if (!b) return;
    b.innerHTML = ico(ai.running ? "stop" : "arrow_upward"); b.setAttribute("aria-label", ai.running ? "Stoppen" : "Senden"); b.classList.toggle("stop", ai.running);
    $("#aim").innerHTML = `<span>${esc(modelName(ai.model))}</span>${ico("expand_more")}`;
  }

  // ---------------------------------------------------------------- Senden und Mitlesen
  async function ndjson(res, onEvent) {
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `Fehler ${res.status}`); }
    const r = res.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await r.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      for (let n; (n = buf.indexOf("\n")) >= 0;) { const l = buf.slice(0, n); buf = buf.slice(n + 1); if (l.trim()) onEvent(JSON.parse(l)); }
    }
  }
  function onEvent(m, ev, onChat) {
    if (ev.chat) { ai.chatId = ev.chat.id; localStorage.setItem("docs.ai." + ai.docId, ev.chat.id); onChat?.(); }
    else if (ev.text !== undefined) { m.text += ev.text; ai.status = ""; }
    else if (ev.tool) ai.status = `Nutzt ${ev.tool.name} …`;
    else if (ev.sys) ai.status = ev.sys;
    else if (ev.done && !ev.done.ok && !ev.done.stopped) m.error = ev.done.error || "Ida konnte nicht antworten";
    later();
  }
  async function run(m, request, onChat) {
    const doc = ai.docId, ctrl = (ai.ctrl = new AbortController());
    ai.running = true; ai.status = ""; drawMsgs(); drawInput();
    try { await ndjson(await request(ctrl.signal), (ev) => { if (ai.docId === doc) onEvent(m, ev, onChat); }); }
    catch (e) { if (e.name !== "AbortError" && ai.docId === doc) m.error = e.message === "Failed to fetch" ? "Keine Verbindung zu Ida" : e.message; }
    if (ai.docId !== doc || ctrl !== ai.ctrl) return;
    ai.running = false; ai.status = ""; ai.ctrl = null; clearTimeout(timer); timer = 0; drawMsgs(); drawInput();
  }
  // "/" am Anfang: Skills aus IDA (z. B. /humanizer)
  let slash = [], slashIdx = 0;
  const skills = () => ai.models?.skills || [];
  function drawSlash() {
    const box = $("#aislash"); if (!box) return;
    const m = /^\/([a-z0-9-]*)$/.exec($("#aitxt").value);
    slash = m ? skills().filter((s) => s.id.startsWith(m[1]) || s.name.toLowerCase().startsWith(m[1])) : [];
    box.hidden = !slash.length; if (!slash.length) return;
    slashIdx = Math.min(slashIdx, slash.length - 1);
    box.innerHTML = slash.map((s, i) => `<button type="button" data-sk="${esc(s.id)}" class="${i === slashIdx ? "on" : ""}">${ico(s.icon || "auto_fix_high")}<span><b>/${esc(s.id)}</b><small>${esc(s.description || s.name)}</small></span></button>`).join("");
  }
  function pickSlash(id) { const i = $("#aitxt"); i.value = `/${id} `; $("#aislash").hidden = true; slash = []; i.focus(); }
  function send() {
    const input = $("#aitxt"); let q = input.value.trim(), skill = null;
    const sm = /^\/([a-z0-9-]+)(?:\s+([\s\S]*))?$/.exec(q);
    if (sm && skills().some((s) => s.id === sm[1])) { skill = sm[1]; q = (sm[2] || "").trim(); }
    if ((!q && !skill) || ai.running) return;
    const ctx = context(), text = q + MARK + ctx.text;
    ai.msgs.push({ role: "user", text: (skill ? `/${skill} ` : "") + text });
    const m = { role: "assistant", text: "" }; ai.msgs.push(m);
    input.value = ""; input.style.height = ""; $("#aimsgs").scrollTop = 1e9;
    run(m, (signal) => fetch("/api/v1/ida/chat", { method: "POST", signal, headers: { "content-type": "application/json" }, body: JSON.stringify({ chat_id: ai.chatId, agent_id: ai.chatId ? null : "ida", text, model: ai.model || null, skill }) }), () => { ai.lastFull = ctx.full; ai.lastOthers = ctx.othersKey; });
  }
  async function load() {
    ai.loaded = true;
    if (!ai.chatId) return drawMsgs();
    const doc = ai.docId;
    try {
      const c = await api(`/ida/chats/${ai.chatId}`); if (ai.docId !== doc) return;
      ai.msgs = c.messages.filter((m) => m.role === "user" || m.role === "assistant").map((m) => ({ role: m.role, text: m.text || "" }));
      if (c.running) {
        const m = ai.msgs.at(-1)?.role === "assistant" ? ai.msgs.at(-1) : (ai.msgs.push({ role: "assistant", text: "" }), ai.msgs.at(-1)); m.text = "";
        run(m, (signal) => fetch(`/api/v1/ida/chats/${ai.chatId}/live`, { signal }));
      }
    } catch { ai.chatId = null; ai.msgs = []; localStorage.removeItem("docs.ai." + doc); }
    drawMsgs();
  }
  async function models() {
    if (ai.models) return;
    try { ai.models = await api("/ida/bootstrap"); drawInput(); } catch { /* Knopf zeigt dann nur Standard */ }
  }
  function modelMenu(btn) {
    const r = btn.getBoundingClientRect(), scrim = el('<div class="scrim" style="background:none"></div>');
    const ids = ["", ...(ai.models?.models || [])];
    const m = el(`<div class="aimenu" style="top:${r.bottom + 6}px;left:${Math.max(8, Math.min(r.left, innerWidth - 300))}px">${ids.map((id) => `<button data-id="${esc(id)}" class="${id === ai.model ? "on" : ""}"><span>${esc(modelName(id))}</span>${id === ai.model ? ico("check") : ""}</button>`).join("")}${ai.models ? "" : '<p class="fld"><small>Modelle konnten nicht geladen werden.</small></p>'}</div>`);
    document.body.append(scrim, m);
    const close = () => { scrim.remove(); m.remove(); };
    scrim.onclick = close;
    m.onclick = (e) => { const b = e.target.closest("[data-id]"); if (!b) return; ai.model = b.dataset.id; localStorage.setItem("docs.ai.model", ai.model); close(); drawInput(); };
  }
  function toggle(on) {
    const sp = $(".split"), show = on ?? !open();
    sp.classList.toggle("aion", show);
    localStorage.setItem("docs.ai.open", show ? "1" : "0");
    $("#aib").classList.toggle("on", show);
    if (show) { if (!ai.loaded) load(); models(); if (!mobile()) $("#aitxt").focus(); }
    drawScope();
  }
  function track() {
    const t = ta(), s = t.selectionStart, e = t.selectionEnd;
    if (e > s) ai.sel = { from: s, to: e, text: t.value.slice(s, e) };
    else if (document.activeElement === t) ai.sel = null;
    drawScope();
  }

  return {
    html: `<div class="card ai"><header class="aih"><b>Ida</b><button class="aim" id="aim" aria-label="Modell wählen" title="Modell wählen"></button><span class="grow"></span><button class="btn ic" id="ainew" aria-label="Neuer Chat" title="Neuer Chat">${ico("add_comment")}</button><button class="btn ic" id="aix" aria-label="Schließen" title="Schließen">${ico("close")}</button></header>
      <div class="aimsgs" id="aimsgs"></div><div class="aiscope" id="aiscope"></div>
      <div class="slashm" id="aislash" hidden></div><div class="aiinp"><textarea id="aitxt" rows="1" placeholder="Frag Ida oder lass etwas umschreiben …"></textarea><button class="aisend" id="aisend"></button></div></div>`,
    pill: `<button class="aipill" id="aipill" hidden>${ico("auto_awesome")}Ida fragen</button>`,
    setup(id) {
      ai.ctrl?.abort();
      Object.assign(ai, { docId: id, chatId: localStorage.getItem("docs.ai." + id), msgs: [], sel: null, scope: "doc", running: false, ctrl: null, lastFull: "", lastOthers: "", status: "", applied: new Set(), loaded: false });
      const t = ta(), input = $("#aitxt");
      for (const ev of ["select", "keyup", "mouseup", "input"]) t.addEventListener(ev, track);
      $("#aib").onclick = () => (mobile() ? $('.mtabs [data-m="ai"]').click() : toggle());
      $("#aix").onclick = () => (mobile() ? $('.mtabs [data-m="src"]').click() : toggle(false));
      $("#aipill").onmousedown = (e) => e.preventDefault();
      $("#aipill").onclick = () => { if (mobile()) $('.mtabs [data-m="ai"]').click(); else toggle(true); $("#aitxt").focus(); };
      $("#aim").onclick = async (e) => { const b = e.currentTarget; await models(); modelMenu(b); };
      $("#ainew").onclick = () => { if (ai.running) return toast("Ida schreibt noch"); ai.chatId = null; ai.msgs = []; ai.lastFull = ""; ai.lastOthers = ""; ai.applied = new Set(); localStorage.removeItem("docs.ai." + id); drawMsgs(); input.focus(); };
      $("#aisend").onclick = () => { if (ai.running) { if (ai.chatId) fetch(`/api/v1/ida/chats/${ai.chatId}/stop`, { method: "POST" }); } else send(); };
      input.onkeydown = (e) => {
        if (slash.length && !$("#aislash").hidden) {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); slashIdx = (slashIdx + (e.key === "ArrowDown" ? 1 : -1) + slash.length) % slash.length; drawSlash(); return; }
          if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pickSlash(slash[slashIdx].id); return; }
          if (e.key === "Escape") { e.preventDefault(); $("#aislash").hidden = true; slash = []; return; }
        }
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } };
      input.oninput = () => { input.style.height = "auto"; input.style.height = Math.min(160, input.scrollHeight) + "px"; slashIdx = 0; if (!ai.models) models().then(drawSlash); drawSlash(); };
      $("#aislash").onmousedown = (e) => { const b = e.target.closest("[data-sk]"); if (b) { e.preventDefault(); pickSlash(b.dataset.sk); } };
      $("#aiscope").onclick = (e) => { if (e.target.closest("[data-x]")) { ai.sel = null; drawScope(); return; } const b = e.target.closest("[data-s]"); if (b) { ai.scope = b.dataset.s; drawScope(); } };
      $("#aimsgs").onclick = (e) => {
        const b = e.target.closest("[data-apply]"); if (!b) return;
        const [i, k] = b.dataset.apply.split(":").map(Number), p = parts(ai.msgs[i].text)[k];
        if (p?.prop != null && apply(i, p.prop)) { ai.applied.add(`${i}:${k}`); drawMsgs(); }
      };
      drawMsgs(); drawScope(); drawInput();
      toggle(localStorage.getItem("docs.ai.open") === "1" && !mobile());
    },
    shown() { if (!ai.loaded) load(); models(); drawScope(); },
    tabChanged() { ai.sel = null; drawScope(); },
    stop() { ai.ctrl?.abort(); ai.ctrl = null; ai.docId = null; ai.running = false; },
  };
}
