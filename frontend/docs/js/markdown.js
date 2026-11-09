// Markdown → HTML für IDA Docs. Eigener kleiner Parser (keine Fremdbibliothek), dazu Erweiterungen für Dokumente:
//   [TOC]            Inhaltsverzeichnis        \newpage   Seitenumbruch        # Titel {-}   Überschrift ohne Nummer
//   ::: center|right|left|justify|title … :::   ausgerichteter Block / Titelblatt       [^1] + [^1]: Text   Fußnoten
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const safeUrl = (u) => (/^\s*(javascript|data|vbscript):/i.test(u) && !/^\s*data:image\//i.test(u) ? "#" : u);

export function inline(s, fn) {
  const codes = [];
  s = esc(s).replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
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

/** @returns {{html:string, headings:{id:string,level:number,num:string,text:string}[]}} */
export function render(md, { numbers = true } = {}) {
  const notes = {}, order = [];
  const lines = md.replace(/\r\n?/g, "\n").split("\n").filter((l) => {
    const m = l.match(/^\[\^([^\]\s]+)\]:\s*(.*)$/);
    if (m) { notes[m[1]] = m[2]; return false; }
    return true;
  });
  const fn = { ref: (k) => { if (!(k in notes)) return 0; if (!order.includes(k)) order.push(k); return order.indexOf(k) + 1; } };
  const headings = [], cnt = [0, 0, 0];
  let tocAt = false;

  function list(start, ind) {
    let i = start; const first = lines[i];
    const ordered = /^\s*\d+[.)]\s/.test(first); const tag = ordered ? "ol" : "ul";
    let out = `<${tag}>`;
    while (i < lines.length) {
      const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (!m) break;
      const level = m[1].replace(/\t/g, "  ").length;
      if (level < ind) break;
      if (level > ind) { const [h, ni] = list(i, level); out = out.replace(/<\/li>$/, "") + h + "</li>"; i = ni; continue; }
      if ((/\d/.test(m[2]) ? "ol" : "ul") !== tag) break;
      out += `<li>${inline(m[3], fn)}</li>`; i++;
    }
    return [out + `</${tag}>`, i];
  }

  function blocks(ls) {
    const saved = lines.splice(0, lines.length, ...ls);   // lines wird in list() gebraucht
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      if (!l.trim()) { i++; continue; }
      let m;
      if ((m = l.match(/^```(\w*)/))) {
        const buf = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
        i++; out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`); continue;
      }
      if ((m = l.match(/^:::\s*(center|right|left|justify|title)\s*$/))) {
        const buf = []; i++;
        while (i < lines.length && !/^:::\s*$/.test(lines[i])) buf.push(lines[i++]);
        i++; const inner = buf;
        out.push({ box: m[1], buf: inner }); continue;
      }
      if (/^\\(newpage|pagebreak)\s*$/i.test(l)) { out.push('<div class="pb"></div>'); i++; continue; }
      if (/^\[TOC\]\s*$/i.test(l)) { out.push("@@TOC@@"); tocAt = true; i++; continue; }
      if ((m = l.match(/^(#{1,4})\s+(.*?)\s*$/))) {
        const level = m[1].length; let text = m[2], num = "";
        const plain = /\s\{-\}$/.test(text); text = text.replace(/\s*\{-\}$/, "");
        if (numbers && !plain && level <= 3) {
          cnt[level - 1]++; for (let k = level; k < 3; k++) cnt[k] = 0;
          num = cnt.slice(0, level).join(".") + (level === 1 ? "." : "");
        }
        const id = `h-${headings.length}-${slug(text)}`;
        if (level <= 3) headings.push({ id, level, num, text });
        out.push(`<h${level} id="${id}">${num ? `<span class="num">${num}</span> ` : ""}${inline(text, fn)}</h${level}>`);
        i++; continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
      if (/^>/.test(l)) {
        const buf = [];
        while (i < lines.length && /^>/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ""));
        out.push({ quote: buf }); continue;
      }
      if (i + 1 < lines.length && isRow(l) && isSep(lines[i + 1])) {
        const head = cells(l), al = cells(lines[i + 1]).map((c) => (/^:-+:$/.test(c) ? "center" : /-:$/.test(c) ? "right" : /^:-/.test(c) ? "left" : ""));
        i += 2; const rows = [];
        while (i < lines.length && lines[i].trim() && isRow(lines[i])) rows.push(cells(lines[i++]));
        const td = (t, c, k) => `<${t}${al[k] ? ` style="text-align:${al[k]}"` : ""}>${inline(c, fn)}</${t}>`;
        out.push(`<div class="tw"><table><thead><tr>${head.map((c, k) => td("th", c, k)).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${head.map((_, k) => td("td", r[k] || "", k)).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
        const [h, ni] = list(i, l.match(/^\s*/)[0].replace(/\t/g, "  ").length); out.push(h); i = ni; continue;
      }
      const buf = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|>|:::|\\(newpage|pagebreak)|\[TOC\])/i.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) && !(i + 1 < lines.length && isRow(lines[i]) && isSep(lines[i + 1]))) buf.push(lines[i++]);
      if (!buf.length) { buf.push(lines[i++]); }
      const txt = buf.join("\n");
      const img = txt.match(/^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)$/);
      out.push(img ? `<figure><img src="${safeUrl(esc(img[2]))}" alt="${esc(img[1])}">${img[1] ? `<figcaption>${inline(img[1], fn)}</figcaption>` : ""}</figure>`
        : `<p>${inline(txt, fn).replace(/ {2,}\n|\\\n/g, "<br>").replace(/\n/g, " ")}</p>`);
    }
    lines.splice(0, lines.length, ...saved);
    return out.map((o) => {
      if (typeof o === "string") return o;
      if (o.box) return `<div class="box ${o.box}">${blocks(o.buf)}</div>`;
      return `<blockquote>${blocks(o.quote)}</blockquote>`;
    }).join("\n");
  }

  let html = blocks(lines);
  if (tocAt) {
    const items = headings.filter((h) => h.level <= 3).map((h) =>
      `<a class="toc-l${h.level}" href="#${h.id}"><span class="t">${h.num ? `<span class="num">${h.num}</span> ` : ""}${esc(h.text)}</span><span class="dots"></span><span class="pg" data-h="${h.id}">0</span></a>`).join("");
    html = html.replace("@@TOC@@", `<nav class="toc"><div class="toc-title">Inhaltsverzeichnis</div>${items || '<p class="muted">Noch keine Überschriften.</p>'}</nav>`);
  }
  if (order.length) html += `<div class="fns"><hr><ol>${order.map((k) => `<li>${inline(notes[k], { ref: () => 0 })}</li>`).join("")}</ol></div>`;
  return { html, headings };
}
