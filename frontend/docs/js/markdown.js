// Markdown → HTML für IDA Docs. Eigener kleiner Parser (keine Fremdbibliothek), dazu Erweiterungen für Dokumente:
//   [TOC]            Inhaltsverzeichnis        \newpage   Seitenumbruch        # Titel {-}   Überschrift ohne Nummer
//   ::: center|right|left|justify|title … :::   ausgerichteter Block / Titelblatt       [^1] + [^1]: Text   Fußnoten
//   [[Notiz]]  (nur im Notizen-Panel, nie im Dokument)   ::: entwurf … :::  (noch nicht fertiger Text, auf dem Bildschirm markiert)
//   [@schluessel] / [@schluessel, 4]   Zitat aus dem Quellenarchiv      [QUELLEN]   Quellenverzeichnis aus den zitierten Quellen
//   Mehr als eine leere Zeile zwischen Blöcken: jede weitere leere Zeile bleibt als Leerzeile sichtbar.
//   Jeder Block bekommt data-l / data-e (Start- und Endzeile im Markdown) – daraus entstehen die Seitenmarker im Editor.
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const safeUrl = (u) => (/^\s*(javascript|data|vbscript):/i.test(u) && !/^\s*data:image\//i.test(u) ? "#" : u);

// ---------------------------------------------------------------- Quellen
const authorsOf = (s) => String(s.author || "").split(/\s*;\s*|\s+und\s+/).map((a) => a.trim()).filter(Boolean);
const surname = (a) => a.split(",")[0].trim();
const given = (a) => (a.includes(",") ? a.split(",").slice(1).join(",").trim() : "");
const dmy = (d) => (/^\d{4}-\d{2}-\d{2}$/.test(d || "") ? d.split("-").reverse().join(".") : d || "");
const tidy = (t) => t.replace(/\s+/g, " ").replace(/([.?!])\./g, "$1").replace(/\s+([.,])/g, "$1").trim();

/** Kurzbeleg: "Müller 2020", "Müller u. a. 2020", ohne Autor der Titelanfang. */
export function citeShort(s) {
  const a = authorsOf(s), year = s.year || "o. J.";
  const who = a.length ? surname(a[0]) + (a.length > 1 ? " u. a." : "") : (s.title || s.key || "").slice(0, 32);
  return `${who} ${year}`;
}
/** Eintrag im Quellenverzeichnis (mit *kursiv*), style = "din" oder "apa". */
export function bibEntry(s, style = "din") {
  const a = authorsOf(s), year = s.year || "o. J.", t = s.title || "(ohne Titel)", type = s.type || "buch";
  const acc = s.accessed ? `abgerufen am ${dmy(s.accessed)}` : "";
  if (style === "apa") {
    const ini = (g) => g.split(/[\s-]+/).filter(Boolean).map((x) => x[0].toUpperCase() + ".").join(" ");
    const names = a.map((x) => (given(x) ? `${surname(x)}, ${ini(given(x))}` : x));
    const who = names.length > 2 ? names.slice(0, -1).join(", ") + ", & " + names.at(-1) : names.join(" & ");
    const head = `${who ? who + " " : ""}(${year}).`;
    if (type === "artikel") return tidy(`${head} ${t}. *${s.container || ""}*${s.note ? ", " + s.note : ""}.`);
    if (type === "web") return tidy(`${head} *${t}*. ${s.publisher ? s.publisher + ". " : ""}${acc ? acc.charAt(0).toUpperCase() + acc.slice(1) + ", von " : ""}${s.url || ""}`);
    return tidy(`${head} *${t}*.${s.place ? " " + s.place + ":" : ""} ${s.publisher || ""}.${s.url ? " " + s.url : ""}`);
  }
  const who = a.join("; ");
  const head = `${who ? who + " " : ""}(${year}): `;
  if (type === "artikel") return tidy(`${head}${t}. In: *${s.container || ""}*${s.note ? ", " + s.note : ""}.`);
  if (type === "web") return tidy(`${head}${t}.${s.publisher ? " " + s.publisher + "." : ""} Online unter: ${s.url || ""}${acc ? " (" + acc + ")" : ""}.`);
  if (type === "buch") return tidy(`${head}*${t}*.${s.place || s.publisher ? " " : ""}${s.place ? s.place + (s.publisher ? ": " : "") : ""}${s.publisher || ""}.${s.url ? " " + s.url + (acc ? " (" + acc + ")" : "") + "." : ""}`);
  return tidy(`${head}${t}.${s.publisher ? " " + s.publisher + "." : ""}${s.url ? " " + s.url + (acc ? " (" + acc + ")" : "") + "." : ""}`);
}
export const sortSources = (list) => [...list].sort((x, y) => (surname(authorsOf(x)[0] || x.title || "") + (x.year || "")).localeCompare(surname(authorsOf(y)[0] || y.title || "") + (y.year || ""), "de"));

// ---------------------------------------------------------------- Zeilenelemente
const CITE = /\[@([^\],\s]+)(?:,\s*([^\]]*))?\]/g;
export function inline(s, fn) {
  const codes = [];
  s = esc(s).replace(/\s?\[\[[^\]]*\]\]/g, "").replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(CITE, (m, k, loc) => (fn.cite ? fn.cite(k, loc) : m));
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)/g, (_, a, u, t) => `<img src="${safeUrl(u)}" alt="${a}"${t ? ` title="${t}"` : ""}>`)
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, u) => `<a href="${safeUrl(u)}" target="_blank" rel="noopener">${t}</a>`)
    .replace(/\[\^([^\]\s]+)\]/g, (_, k) => { const n = fn.ref(k); return n ? `<sup class="fnref">${n}</sup>` : _; })
    .replace(/\*\*([^*]+?)\*\*/g, "<b>$1</b>").replace(/__([^_]+?)__/g, "<b>$1</b>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<i>$2</i>").replace(/(^|[^_\w])_([^_\s][^_]*?)_(?!\w)/g, "$1<i>$2</i>")
    .replace(/~~([^~]+)~~/g, "<s>$1</s>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

const isRow = (l) => /^\s*\|.*\|\s*$/.test(l) || (/\|/.test(l) && !/^\s*$/.test(l));
const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
const isSep = (l) => /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/.test(l) && l.includes("-") && l.includes("|");
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9äöüß]+/g, "-").replace(/^-|-$/g, "");
const STOP = /^(#{1,4}\s|```|>|:::|\\(newpage|pagebreak)|\[(TOC|QUELLEN|LITERATUR)\])/i;
const LI = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

/** @returns {{html:string, headings:{id:string,level:number,num:string,text:string}[], used:string[], missing:string[]}} */
export function render(md, { numbers = true, sources = [], citeStyle = "kurz", bibStyle = "din" } = {}) {
  const notes = {}, order = [], skip = new Set();
  const all = md.replace(/\r\n?/g, "\n").split("\n").map((l, i) => {
    const m = l.match(/^\[\^([^\]\s]+)\]:\s*(.*)$/);
    if (m) { notes[m[1]] = m[2]; skip.add(i); return ""; }
    return l;
  });
  const byKey = new Map(sources.map((s) => [s.key, s])), used = new Set(), missing = new Set(), seen = new Set();
  let cc = 0;
  const fn = {
    ref: (k) => { if (!(k in notes)) return 0; if (!order.includes(k)) order.push(k); return order.indexOf(k) + 1; },
    add: (text) => { const k = "\u0002" + ++cc; notes[k] = text; order.push(k); return order.length; },
    cite: (k, loc) => {
      const s = byKey.get(k);
      if (!s) { missing.add(k); return `<span class="cite bad">(?${esc(k)})</span>`; }
      used.add(k);
      const l = (loc || "").trim(), where = l ? (/^\d/.test(l) ? "S. " + l : l) : "";
      if (citeStyle === "fuss") {
        const first = !seen.has(k); seen.add(k);
        return `<sup class="fnref">${fn.add((first ? bibEntry(s, bibStyle).replace(/\.$/, "") : citeShort(s)) + (where ? ", " + where : "") + ".")}</sup>`;
      }
      return `<span class="cite">(${esc(citeShort(s))}${where ? ", " + esc(where) : ""})</span>`;
    },
  };
  const headings = [], cnt = [0, 0, 0];
  let tocAt = false, bibAt = false;

  function heading(level, text, plain) {
    let num = "";
    if (numbers && !plain && level <= 3) {
      cnt[level - 1]++; for (let k = level; k < 3; k++) cnt[k] = 0;
      num = cnt.slice(0, level).join(".") + (level === 1 ? "." : "");
    }
    const id = `h-${headings.length}-${slug(text)}`;
    if (level <= 3) headings.push({ id, level, num, text });
    return `<h${level} id="${id}">${num ? `<span class="num">${num}</span> ` : ""}${inline(text, fn)}</h${level}>`;
  }

  function list(L, start, ind) {
    let i = start; const ordered = /^\s*\d+[.)]\s/.test(L[i]); const tag = ordered ? "ol" : "ul";
    let out = `<${tag}>`;
    while (i < L.length) {
      const m = L[i].match(LI);
      if (!m) break;
      const level = m[1].replace(/\t/g, "  ").length;
      if (level < ind) break;
      if (level > ind) { const [h, ni] = list(L, i, level); out = out.replace(/<\/li>$/, "") + h + "</li>"; i = ni; continue; }
      if ((/\d/.test(m[2]) ? "ol" : "ul") !== tag) break;
      out += `<li>${inline(m[3], fn)}</li>`; i++;
    }
    return [out + `</${tag}>`, i];
  }

  // L = Zeilen, base = Nummer der ersten Zeile im ganzen Dokument (für data-l)
  function blocks(L, base) {
    const out = [];
    let i = 0, cur = null;
    const seal = () => {   // den eben fertigen Block mit seinen Zeilen kennzeichnen
      if (cur && out.length > cur.n) { const k = out.length - 1; out[k] = out[k].replace(/^<(\w+)/, `<$1 data-l="${cur.a}" data-e="${base + i}"`); }
      cur = null;
    };
    while (i < L.length) {
      seal();
      const l = L[i];
      if (!l.trim()) {   // eine leere Zeile trennt Absätze, jede weitere bleibt als Leerzeile stehen
        let j = i, n = 0;
        while (j < L.length && !L[j].trim()) { if (!skip.has(base + j)) n++; j++; }
        if (out.length && j < L.length) for (let k = 1; k < n; k++) out.push('<p class="blank">&nbsp;</p>');
        i = j; continue;
      }
      cur = { a: base + i, n: out.length };
      let m;
      if ((m = l.match(/^```(\w*)/))) {
        const buf = []; i++;
        while (i < L.length && !/^```/.test(L[i])) buf.push(L[i++]);
        i++; out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`); continue;
      }
      if ((m = l.match(/^:::\s*(center|right|left|justify|title|entwurf)\s*$/))) {
        const s0 = i + 1; i++;
        while (i < L.length && !/^:::\s*$/.test(L[i])) i++;
        out.push(`<div class="box ${m[1]}">${blocks(L.slice(s0, i), base + s0)}</div>`);
        i++; continue;
      }
      if (/^\\(newpage|pagebreak)\s*$/i.test(l)) { out.push('<div class="pb"></div>'); i++; continue; }
      if (/^\[TOC\]\s*$/i.test(l)) { out.push("@@TOC@@"); tocAt = true; i++; continue; }
      if (/^\[(QUELLEN|LITERATUR)\]\s*$/i.test(l)) { out.push(heading(1, "Quellenverzeichnis", true) + "@@BIB@@"); bibAt = true; i++; continue; }
      if ((m = l.match(/^(#{1,4})\s+(.*?)\s*$/))) {
        const plain = /\s\{-\}$/.test(m[2]);
        out.push(heading(m[1].length, m[2].replace(/\s*\{-\}$/, ""), plain));
        i++; continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
      if (/^>/.test(l)) {
        const s0 = i;
        while (i < L.length && /^>/.test(L[i])) i++;
        out.push(`<blockquote>${blocks(L.slice(s0, i).map((x) => x.replace(/^>\s?/, "")), base + s0)}</blockquote>`); continue;
      }
      if (i + 1 < L.length && isRow(l) && isSep(L[i + 1])) {
        const head = cells(l), al = cells(L[i + 1]).map((c) => (/^:-+:$/.test(c) ? "center" : /-:$/.test(c) ? "right" : /^:-/.test(c) ? "left" : ""));
        i += 2; const rows = [];
        while (i < L.length && L[i].trim() && isRow(L[i])) rows.push(cells(L[i++]));
        const td = (t, c, k) => `<${t}${al[k] ? ` style="text-align:${al[k]}"` : ""}>${inline(c, fn)}</${t}>`;
        out.push(`<div class="tw"><table><thead><tr>${head.map((c, k) => td("th", c, k)).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${head.map((_, k) => td("td", r[k] || "", k)).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      if (LI.test(l)) {
        const [h, ni] = list(L, i, l.match(/^\s*/)[0].replace(/\t/g, "  ").length); out.push(h); i = ni; continue;
      }
      const buf = [];
      while (i < L.length && L[i].trim() && !STOP.test(L[i]) && !LI.test(L[i]) && !(i + 1 < L.length && isRow(L[i]) && isSep(L[i + 1]))) buf.push(L[i++]);
      if (!buf.length) { buf.push(L[i++]); }
      const txt = buf.join("\n");
      const img = txt.match(/^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)$/);
      if (img) { out.push(`<figure><img src="${safeUrl(esc(img[2]))}" alt="${esc(img[1])}">${img[1] ? `<figcaption>${inline(img[1], fn)}</figcaption>` : ""}</figure>`); continue; }
      const h = inline(txt, fn);   // nur einmal aufrufen (Fußnoten werden beim Aufruf gezählt)
      if (h.trim()) out.push(`<p>${h.replace(/ {2,}\n|\\\n/g, "<br>").replace(/\n/g, " ")}</p>`);   // ein Absatz nur aus [[Notiz]] fällt weg
    }
    seal();
    return out.join("\n");
  }

  let html = blocks(all, 0);
  if (tocAt) {
    const items = headings.filter((h) => h.level <= 3).map((h) =>
      `<a class="toc-l${h.level}" href="#${h.id}"><span class="t">${h.num ? `<span class="num">${h.num}</span> ` : ""}${esc(h.text)}</span><span class="dots"></span><span class="pg" data-h="${h.id}">0</span></a>`).join("");
    html = html.replace("@@TOC@@", `<nav class="toc"><div class="toc-title">Inhaltsverzeichnis</div>${items || '<p class="muted">Noch keine Überschriften.</p>'}</nav>`);
  }
  if (bibAt) {
    const list = sortSources(sources.filter((s) => used.has(s.key)));
    html = html.replace("@@BIB@@", list.length ? list.map((s) => `<p class="bibe">${inline(bibEntry(s, bibStyle), { ref: () => 0 })}</p>`).join("") : '<p class="muted">Noch keine Quelle zitiert. Zitieren mit [@schluessel].</p>');
  }
  if (order.length) html += `<div class="fns"><hr><ol>${order.map((k) => `<li>${inline(notes[k], { ref: () => 0 })}</li>`).join("")}</ol></div>`;
  return { html, headings, used: [...used], missing: [...missing] };
}
