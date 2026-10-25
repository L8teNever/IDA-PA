// Back button (Android, browser, swipe): first closes whatever is open on top – dropdown, time picker,
// model menu, agent menu, sheet, side menu – and only then goes back a page.
// Every open layer gets its own history entry; closing it by tapping elsewhere removes that entry again,
// so the next "back" really goes to the previous page.
import { closeSheet } from "./dom.js";
import { closeDropdown } from "./select.js";
import { closeTimePicker } from "./timepicker.js";

const B = document.body;
const byId = (id) => document.getElementById(id);
// top-most first
const LAYERS = [
  { isOpen: () => !!document.querySelector(".dd-menu.tp:not(.closing)"), close: closeTimePicker },
  { isOpen: () => !!document.querySelector(".dd-menu:not(.tp):not(.closing)"), close: closeDropdown },
  { isOpen: () => B.classList.contains("slash-open"), close: () => B.classList.remove("slash-open") },
  { isOpen: () => B.classList.contains("model-open"), close: () => B.classList.remove("model-open") },
  { isOpen: () => !!byId("agentWrap")?.classList.contains("open"), close: () => byId("agentWrap").classList.remove("open") },
  { isOpen: () => B.classList.contains("ev-open"), close: closeSheet },
  { isOpen: () => /\bdrawer-(menu|chats|routinen)\b/.test(B.className), close: () => B.classList.remove("drawer-menu", "drawer-chats", "drawer-routinen") },
];

let depth = 0;       // history entries we pushed for open layers
let pending = 0;     // our own history.go(-n) calls that have not arrived yet
let waiters = [];
let scheduled = false;

const openCount = () => LAYERS.filter((l) => l.isOpen()).length;

function sync() {
  scheduled = false;
  const n = openCount();
  if (n > depth) {
    for (; depth < n; depth++) history.pushState({ idaLayer: true }, "", location.href);
  } else if (n < depth) {  // closed by tapping elsewhere: take our entries back out
    pending++;
    history.go(n - depth);
    depth = n;
  }
}

/** Resolves when our own history moves are done – the router waits for it before it pushes a new page. */
export function settled() {
  return pending ? new Promise((r) => waiters.push(r)) : Promise.resolve();
}

/** Before a page change: close everything that is open and take its history entries back out. */
export async function closeAll() {
  LAYERS.forEach((l) => l.isOpen() && l.close());
  sync();
  await settled();
}

export function initBack() {
  new MutationObserver(() => {
    if (!scheduled) { scheduled = true; queueMicrotask(sync); }
  }).observe(B, { attributes: true, attributeFilter: ["class"], childList: true, subtree: true });

  // registered before the router: stops the page change while a layer is closed
  addEventListener("popstate", (e) => {
    if (pending) {
      pending--;
      e.stopImmediatePropagation();
      if (!pending) { waiters.forEach((r) => r()); waiters = []; }
      return;
    }
    if (depth > 0) {
      depth--;
      e.stopImmediatePropagation();
      const top = LAYERS.find((l) => l.isOpen());
      if (top) top.close();
      return;
    }
    if (history.state?.idaLayer) {  // a leftover layer entry (e.g. after a reload): skip it
      e.stopImmediatePropagation();
      history.back();
    }
  });
}
