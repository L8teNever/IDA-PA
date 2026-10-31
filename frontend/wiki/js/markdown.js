// Sicherer, abhängigkeitsfreier Obsidian- & GFM-Markdown-Renderer:
// - [[Wikilinks]], [[Pfad|Anzeigename]], [[Pfad#Abschnitt]] (inkl. Geister-Link-Erkennung)
// - ![[Embeds]] (Bilder, PDFs, Audio/Video, Textdateien & eingebettete Notizen)
// - Obsidian Callouts (> [!note], > [!tip], > [!info], > [!important], > [!warning], > [!caution])
// - Interaktive Checklisten (- [ ] / - [x] mit Zeilenindex zum direkten Abhaken)
// - #tags, Tabellen, Codeblöcke (inkl. farbigem Unified-Diff), <details>-Werkzeugblöcke aus KI-Chats.

const CALLOUT_ICONS = {
  note: "notes",
  info: "info",
  tip: "lightbulb",
  hint: "lightbulb",
  important: "auto_awesome",
  success: "check_circle",
  warning: "warning",
  caution: "error",
  danger: "error",
};

export function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function slugifyHeading(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "abschnitt";
}

function safeHref(raw) {
  const u = String(raw || "").trim();
  if (/^(https?:\/\/|mailto:|\/|#)/i.test(u)) return esc(u);
  return "#";
}

function resolveWikilink(rawTarget, currentSpace, pageIndex) {
  let clean = String(rawTarget || "").trim().replace(/^\/+|\/+$/g, "").replace(/\.md$/i, "");
  let heading = "";
  const hashIdx = clean.indexOf("#");
  if (hashIdx >= 0) {
    heading = clean.slice(hashIdx + 1).trim();
    clean = clean.slice(0, hashIdx).trim();
  }
  if (!clean && heading) {
    return { exists: true, path: "", heading, label: heading };
  }
  const low = clean.toLowerCase();
  if (pageIndex?.byPath?.has(low)) {
    const p = pageIndex.byPath.get(low);
    return { exists: true, path: p.path, title: p.title, space: p.space, heading };
  }
  if (!clean.includes("/") && pageIndex?.bySlug?.has(low)) {
    const candidates = pageIndex.bySlug.get(low);
    const sameSpace = candidates.find((c) => c.space === currentSpace) || candidates[0];
    if (sameSpace) {
      return { exists: true, path: sameSpace.path, title: sameSpace.title, space: sameSpace.space, heading };
    }
  }
  const suggestedPath = clean.includes("/")
    ? clean
    : `${currentSpace || "privat"}/${currentSpace === "memory" ? "konzepte-wissen/" : "00-inbox/"}${clean}`;
  return { exists: false, path: suggestedPath, title: clean.split("/").pop(), heading };
}

export function buildPageIndex(pages = []) {
  const byPath = new Map();
  const bySlug = new Map();
  for (const p of pages) {
    byPath.set(p.path.toLowerCase(), p);
    const slug = (p.slug || p.path.split("/").pop() || "").toLowerCase();
    if (!bySlug.has(slug)) bySlug.set(slug, []);
    bySlug.get(slug).push(p);
  }
  return { byPath, bySlug };
}

function renderEmbed(inner, ctx) {
  const [rawTarget, rawAlias] = inner.split("|");
  const name = rawTarget.trim();
  const alias = (rawAlias || "").trim();
  const att = (ctx.attachments || []).find((a) => a.name.toLowerCase() === name.toLowerCase());
  if (att) {
    const url = esc(ctx.prefixUrl ? ctx.prefixUrl(att.url) : att.url);
    if (att.kind === "image") {
      return `<figure class="md-embed-img" data-att-name="${esc(att.name)}"><img src="${url}" alt="${esc(alias || att.name)}" loading="lazy">${alias ? `<figcaption>${esc(alias)}</figcaption>` : ""}</figure>`;
    }
    if (att.kind === "audio") {
      return `<div class="md-embed-media"><div class="md-embed-head"><span class="ms">attach_file</span><span>${esc(att.name)}</span></div><audio controls preload="metadata" src="${url}"></audio></div>`;
    }
    if (att.kind === "video") {
      return `<div class="md-embed-media"><video controls preload="metadata" src="${url}"></video></div>`;
    }
    const icon = att.kind === "pdf" ? "description" : "attach_file";
    return `<button type="button" class="md-att-chip" data-open-att="${esc(att.name)}"><span class="ms">${icon}</span><span>${esc(alias || att.name)}</span><small>${formatBytes(att.size)}</small></button>`;
  }

  // Eingebettete andere Wiki-Seite (![[pfad]])
  const target = resolveWikilink(name, ctx.space, ctx.pageIndex);
  if (target.exists && target.path && ctx.pageIndex?.byPath) {
    const embedded = ctx.pageIndex.byPath.get(target.path.toLowerCase());
    if (embedded && (ctx.depth || 0) < 1 && embedded.body) {
      const snippet = embedded.body.split("\n").slice(0, 14).join("\n");
      return `<div class="md-transclude"><div class="md-transclude-head"><a href="#" data-wikilink="${esc(embedded.path)}"><span class="ms">link</span> ${esc(alias || embedded.title)}</a></div><div class="md-transclude-body">${renderMarkdown(snippet, { ...ctx, depth: (ctx.depth || 0) + 1 })}</div></div>`;
    }
  }
  return `<a href="#" class="md-wikilink ghost" data-wikilink="${esc(target.path)}" data-ghost="1"><span class="ms">add_link</span>${esc(alias || target.title || name)}</a>`;
}

function renderInline(line, ctx) {
  // 1. Inline-Code zuerst schützen
  const codeTokens = [];
  let work = String(line).replace(/`([^`]+)`/g, (_, code) => {
    const id = `\u0000CODE${codeTokens.length}\u0000`;
    codeTokens.push(`<code>${esc(code)}</code>`);
    return id;
  });

  // 2. Embeds ![[...]] und Wikilinks [[...]] schützen
  const htmlTokens = [];
  const stash = (html) => {
    const id = `\u0000HTML${htmlTokens.length}\u0000`;
    htmlTokens.push(html);
    return id;
  };

  work = work.replace(/!\[\[([^\[\]\n]+)\]\]/g, (_, inner) => stash(renderEmbed(inner, ctx)));
  work = work.replace(/\[\[([^\[\]\n]+)\]\]/g, (_, inner) => {
    const [rawTarget, rawAlias] = inner.split("|");
    const resolved = resolveWikilink(rawTarget, ctx.space, ctx.pageIndex);
    const label = (rawAlias || "").trim() || resolved.title || rawTarget.trim();
    if (!resolved.path && resolved.heading) {
      return stash(`<a href="#h-${esc(slugifyHeading(resolved.heading))}" class="md-wikilink">#${esc(label)}</a>`);
    }
    const cls = resolved.exists ? `md-wikilink space-${esc(resolved.space || "privat")}` : "md-wikilink ghost";
    const ghostAttr = resolved.exists ? "" : ' data-ghost="1"';
    const headAttr = resolved.heading ? ` data-heading="${esc(slugifyHeading(resolved.heading))}"` : "";
    return stash(`<a href="#" class="${cls}" data-wikilink="${esc(resolved.path)}"${ghostAttr}${headAttr}>${esc(label)}</a>`);
  });

  // 3. Normale Markdown-Bilder ![alt](url) und Links [text](url)
  work = work.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, url) =>
    stash(`<img class="md-inline-img" src="${safeHref(url)}" alt="${esc(alt)}" loading="lazy">`),
  );
  work = work.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
    const isExt = /^https?:\/\//i.test(url);
    return stash(
      `<a href="${safeHref(url)}"${isExt ? ' target="_blank" rel="noopener"' : ""} class="md-extlink">${esc(label)}${isExt ? ' <span class="ms ext-ic">open_in_new</span>' : ""}</a>`,
    );
  });

  // 4. Rest HTML-escapen
  work = esc(work);

  // 5. Markierungen (<mark> aus FTS5-Snippets optional nein – hier reines Dokument: ==Highlight==, **fett**, *kursiv*, ~~durch~~, #tags)
  work = work.replace(/==([^=]+)==/g, "<mark>$1</mark>");
  work = work.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  work = work.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  work = work.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  work = work.replace(/(^|[\s(])#([a-zA-ZäöüÄÖÜß][\w\-/äöüÄÖÜß]{0,50})/g, (_, pre, tag) =>
    `${pre}<button type="button" class="md-tag" data-tag="${tag.toLowerCase()}">#${tag}</button>`,
  );

  // 6. Geschützte Tokens zurücksetzen
  work = work.replace(/\u0000HTML(\d+)\u0000/g, (_, idx) => htmlTokens[Number(idx)] || "");
  work = work.replace(/\u0000CODE(\d+)\u0000/g, (_, idx) => codeTokens[Number(idx)] || "");
  return work;
}

function renderCodeBlock(lang, codeLines) {
  const raw = codeLines.join("\n");
  if (lang === "diff") {
    return renderUnifiedDiff(raw);
  }
  const label = lang ? `<span class="code-lang">${esc(lang)}</span>` : `<span></span>`;
  return `<div class="md-codeblock"><div class="md-codehead">${label}<button type="button" class="code-copy" data-copy-code="1" title="Kopieren"><span class="ms">content_copy</span></button></div><pre><code>${esc(raw)}</code></pre></div>`;
}

export function renderUnifiedDiff(diffText) {
  const lines = String(diffText || "").replace(/\r\n/g, "\n").split("\n");
  if (!lines.length || (lines.length === 1 && !lines[0])) {
    return `<div class="diff-empty">Keine Textunterschiede in dieser Version.</div>`;
  }
  const out = lines.map((ln) => {
    let cls = "diff-ctx";
    if (ln.startsWith("+++") || ln.startsWith("---")) cls = "diff-hdr";
    else if (ln.startsWith("@@")) cls = "diff-hunk";
    else if (ln.startsWith("+")) cls = "diff-add";
    else if (ln.startsWith("-")) cls = "diff-del";
    return `<div class="diff-line ${cls}">${esc(ln) || " "}</div>`;
  });
  return `<div class="diff-view">${out.join("")}</div>`;
}

export function renderMarkdown(markdown, ctx = {}) {
  const src = String(markdown || "").replace(/\r\n/g, "\n");
  const lines = src.split("\n");
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const ln = lines[i];

    if (!ln.trim()) {
      i += 1;
      continue;
    }

    // Fenced Codeblock ```
    const fence = ln.match(/^```([\w+-]*)\s*$/);
    if (fence) {
      const lang = (fence[1] || "").toLowerCase();
      const buf = [];
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      out.push(renderCodeBlock(lang, buf));
      continue;
    }

    // Kontrolliertes <details><summary>...</summary> für gespiegelte IDA-Tool-Calls in `ki/`
    const detMatch = ln.match(/^<details><summary>(.*)<\/summary>\s*$/i);
    if (detMatch) {
      const sumRaw = detMatch[1].replace(/<\/?code>/gi, "`");
      const innerLines = [];
      i += 1;
      while (i < lines.length && !/^<\/details>\s*$/i.test(lines[i])) {
        innerLines.push(lines[i]);
        i += 1;
      }
      i += 1;
      out.push(
        `<details class="md-details"><summary>${renderInline(sumRaw, ctx)}</summary><div class="md-details-body">${renderMarkdown(innerLines.join("\n"), ctx)}</div></details>`,
      );
      continue;
    }

    // Horizontale Linie
    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(ln.trim())) {
      out.push("<hr>");
      i += 1;
      continue;
    }

    // Überschriften # .. ######
    const hMatch = ln.match(/^(#{1,6})\s+(.+?)\s*$/);
    if (hMatch) {
      const lvl = hMatch[1].length;
      const slug = slugifyHeading(hMatch[2]);
      out.push(`<h${lvl} id="h-${esc(slug)}">${renderInline(hMatch[2], ctx)}</h${lvl}>`);
      i += 1;
      continue;
    }

    // Callout oder Blockquote >
    if (ln.trimStart().startsWith(">")) {
      const qLines = [];
      while (i < lines.length && lines[i].trimStart().startsWith(">")) {
        qLines.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      const callout = qLines[0]?.match(/^\[!([\w-]+)\]\s*(.*)$/);
      if (callout) {
        const kind = callout[1].toLowerCase();
        const title = callout[2].trim() || kind.toUpperCase();
        const icon = CALLOUT_ICONS[kind] || "info";
        const bodyMd = qLines.slice(1).join("\n");
        out.push(
          `<aside class="md-callout callout-${esc(kind)}"><div class="md-callout-title"><span class="ms">${icon}</span><span>${renderInline(title, ctx)}</span></div>${bodyMd.trim() ? `<div class="md-callout-body">${renderMarkdown(bodyMd, ctx)}</div>` : ""}</aside>`,
        );
      } else {
        out.push(`<blockquote>${renderMarkdown(qLines.join("\n"), ctx)}</blockquote>`);
      }
      continue;
    }

    // GFM-Tabelle
    if (ln.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(lines[i + 1])) {
      // Zellen nur an „echten“ Trennstrichen teilen: nicht innerhalb von [[Pfad|Name]], `Code` oder bei \|
      const parseRow = (r) => {
        const s = r.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "");
        const cells = [];
        let cur = "", link = 0, code = false;
        for (let k = 0; k < s.length; k++) {
          const ch = s[k];
          if (ch === "\\" && s[k + 1] === "|") { cur += "|"; k++; continue; }
          if (!code && ch === "[" && s[k + 1] === "[") { link++; cur += "[["; k++; continue; }
          if (!code && link && ch === "]" && s[k + 1] === "]") { link--; cur += "]]"; k++; continue; }
          if (ch === "`") code = !code;
          if (ch === "|" && !link && !code) { cells.push(cur.trim()); cur = ""; continue; }
          cur += ch;
        }
        cells.push(cur.trim());
        return cells;
      };
      const headers = parseRow(ln);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(parseRow(lines[i]));
        i += 1;
      }
      const thHtml = headers.map((h) => `<th>${renderInline(h, ctx)}</th>`).join("");
      const trHtml = rows
        .map((r) => `<tr>${headers.map((_, idx) => `<td>${renderInline(r[idx] || "", ctx)}</td>`).join("")}</tr>`)
        .join("");
      out.push(`<div class="md-table-wrap"><table><thead><tr>${thHtml}</tr></thead><tbody>${trHtml}</tbody></table></div>`);
      continue;
    }

    // Listen & interaktive Checklisten
    if (/^\s*([-*+]|\d+\.)\s+/.test(ln)) {
      const ordered = /^\s*\d+\.\s+/.test(ln);
      const tag = ordered ? "ol" : "ul";
      const items = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        const lineIndex = i;
        const itemText = lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, "");
        const chk = itemText.match(/^\[([ xX])\]\s+(.*)$/);
        if (chk) {
          const checked = chk[1].toLowerCase() === "x";
          items.push(
            `<li class="md-task ${checked ? "done" : ""}"><label><input type="checkbox" ${checked ? "checked" : ""} data-task-line="${lineIndex}" ${ctx.readonlyTasks ? "disabled" : ""}><span>${renderInline(chk[2], ctx)}</span></label></li>`,
          );
        } else {
          items.push(`<li>${renderInline(itemText, ctx)}</li>`);
        }
        i += 1;
      }
      out.push(`<${tag} class="md-list">${items.join("")}</${tag}>`);
      continue;
    }

    // Normaler Absatz
    const pLines = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,6}\s|```|\s*>\s?|\s*([-*+]|\d+\.)\s|<details>)/i.test(lines[i])
    ) {
      pLines.push(renderInline(lines[i], ctx));
      i += 1;
    }
    out.push(`<p>${pLines.join("<br>")}</p>`);
  }

  return out.join("\n");
}

export function toggleCheckboxAtLine(markdown, lineIndex) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  if (lineIndex < 0 || lineIndex >= lines.length) return markdown;
  lines[lineIndex] = lines[lineIndex].replace(/^(\s*[-*+]\s+\[)([ xX])(\]\s+)/, (_, a, state, c) =>
    `${a}${state === " " ? "x" : " "}${c}`,
  );
  return lines.join("\n");
}

export function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
