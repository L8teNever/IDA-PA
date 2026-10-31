// Custom dropdowns in the IDA design for every <select> of the app.
// The native <select> stays in the DOM (hidden) and keeps the value, so page code that reads `.value` or listens
// to "change" works unchanged. New selects are enhanced automatically (MutationObserver).
const OPEN_CLASS = "dd-open";
let openMenu = null; // { menu, btn, select, close }

function labelOf(select) {
  const o = select.options[select.selectedIndex];
  return o ? o.textContent : "";
}

function closeOpen() {
  if (openMenu) openMenu.close();
}

// Closes the open dropdown (back button).
export const closeDropdown = closeOpen;

function place(menu, btn) {
  // align with the whole field box (design ".fld") when there is one
  const r = (btn.closest(".fld") || btn).getBoundingClientRect();
  const below = innerHeight - r.bottom - 12, above = r.top - 12;
  const want = Math.min(menu.scrollHeight, 320);
  const up = below < Math.min(want, 200) && above > below;
  menu.style.left = `${Math.max(8, Math.min(r.left, innerWidth - Math.max(r.width, 200) - 8))}px`;
  menu.style.minWidth = `${Math.max(r.width, 200)}px`;
  menu.style.maxHeight = `${Math.max(140, Math.min(320, up ? above : below))}px`;
  menu.style.top = up ? "" : `${r.bottom + 6}px`;
  menu.style.bottom = up ? `${innerHeight - r.top + 6}px` : "";
  menu.classList.toggle("up", up);
}

function open(select, btn) {
  closeOpen();
  const menu = document.createElement("div");
  menu.className = "dd-menu";
  menu.setAttribute("role", "listbox");
  let active = select.selectedIndex;
  const items = [...select.options].map((o, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "dd-item" + (i === select.selectedIndex ? " sel" : "");
    b.disabled = o.disabled;
    b.setAttribute("role", "option");
    b.innerHTML = `<span class="dd-text"></span><span class="ms dd-check">check</span>`;
    b.querySelector(".dd-text").textContent = o.textContent;
    b.onclick = () => choose(i);
    menu.appendChild(b);
    return b;
  });
  const mark = () => items.forEach((b, i) => b.classList.toggle("act", i === active));
  const choose = (i) => {
    if (select.selectedIndex !== i) {
      select.selectedIndex = i;
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    close();
    btn.focus();
  };
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); btn.focus(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const d = e.key === "ArrowDown" ? 1 : -1;
      for (let n = 0; n < items.length; n++) {
        active = (active + d + items.length) % items.length;
        if (!items[active].disabled) break;
      }
      mark();
      items[active]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (items[active] && !items[active].disabled) choose(active);
    } else if (e.key === "Tab") close();
  };
  const onOutside = (e) => { if (!menu.contains(e.target) && !btn.contains(e.target)) close(); };
  const onMove = () => close();
  function close() {
    if (openMenu?.menu !== menu) return;
    openMenu = null;
    btn.setAttribute("aria-expanded", "false");
    btn.closest(".dd")?.classList.remove(OPEN_CLASS);
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("pointerdown", onOutside, true);
    removeEventListener("resize", onMove);
    document.removeEventListener("scroll", onScrollOutside, true);
    menu.classList.add("closing");
    setTimeout(() => menu.remove(), 160);
  }
  const onScrollOutside = (e) => { if (!menu.contains(e.target)) close(); };
  document.body.appendChild(menu);
  place(menu, btn);
  requestAnimationFrame(() => menu.classList.add("show"));
  btn.setAttribute("aria-expanded", "true");
  btn.closest(".dd")?.classList.add(OPEN_CLASS);
  mark();
  items[active]?.scrollIntoView({ block: "nearest" });
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("pointerdown", onOutside, true);
  addEventListener("resize", onMove);
  document.addEventListener("scroll", onScrollOutside, true);
  openMenu = { menu, btn, select, close };
}

export function enhance(select) {
  if (select.dataset.dd || select.multiple || select.closest(".no-dd")) return;
  select.dataset.dd = "1";
  const wrap = document.createElement("span");
  wrap.className = "dd";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "dd-btn";
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-expanded", "false");
  if (select.title) btn.title = select.title;
  btn.innerHTML = `<span class="dd-label"></span><span class="ms dd-chev">expand_more</span>`;
  const sync = () => {
    btn.querySelector(".dd-label").textContent = labelOf(select);
    btn.disabled = select.disabled;
  };
  select.parentNode.insertBefore(wrap, select);
  wrap.append(btn, select);
  select.classList.add("dd-native");
  select.tabIndex = -1;
  sync();
  btn.onclick = () => (openMenu?.select === select ? closeOpen() : open(select, btn));
  btn.addEventListener("keydown", (e) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key) && openMenu?.select !== select) { e.preventDefault(); open(select, btn); }
  });
  select.addEventListener("change", sync);
  // options or value replaced by page code
  new MutationObserver(sync).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
  const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  Object.defineProperty(select, "value", { configurable: true, get() { return desc.get.call(this); }, set(v) { desc.set.call(this, v); sync(); } });
}

export function initSelects(root = document.body) {
  root.querySelectorAll("select").forEach(enhance);
  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType !== 1) continue;
      if (n.tagName === "SELECT") enhance(n);
      else n.querySelectorAll?.("select").forEach(enhance);
    }
  }).observe(root, { childList: true, subtree: true });
}
