// Small history-API router: every page has a real URL (/chats, /einstellungen/agents/lea …).
import { closeAll, settled } from "./ui/back.js";
const routes = [];
let notFound = null;
let current = null;

// add("/einstellungen/agents/:id", handler) – handler(params, query)
export function add(pattern, handler) {
  const keys = [];
  const re = new RegExp("^" + pattern.replace(/\/:(\w+)/g, (_, k) => { keys.push(k); return "/([^/]+)"; }) + "/?$");
  routes.push({ re, keys, handler });
}
export function fallback(handler) { notFound = handler; }

export async function resolve() {
  const path = location.pathname;
  const query = Object.fromEntries(new URLSearchParams(location.search));
  for (const r of routes) {
    const m = path.match(r.re);
    if (m) {
      const params = Object.fromEntries(r.keys.map((k, i) => [k, decodeURIComponent(m[i + 1])]));
      current = path;
      await r.handler(params, query);
      return;
    }
  }
  if (notFound) await notFound();
}

// position in the app's own history (0 = first page of this visit)
const idx = () => history.state?.i || 0;
const stateFor = (push) => ({ i: idx() + (push ? 1 : 0) });
export const canGoBack = () => idx() > 0;

export async function navigate(url, { replace = false } = {}) {
  await closeAll(); // open menus/sheets close first and take their history entries back out
  if (url === location.pathname + location.search && !replace) return resolve();
  history[replace ? "replaceState" : "pushState"](stateFor(!replace), "", url);
  return resolve();
}

// Only changes the URL (e.g. a new chat got its id) without rendering again.
// push: a new history entry, so "back" returns to the page before (start page -> new chat).
export async function setUrl(url, { push = false } = {}) {
  await (push ? closeAll() : settled());
  history[push ? "pushState" : "replaceState"](stateFor(push), "", url);
  current = url;
}
export const path = () => current;

export function start() {
  addEventListener("popstate", resolve);
  // Internal links: <a href="/…"> and [data-href]
  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const a = e.target.closest("a[href], [data-href]");
    if (!a) return;
    const href = a.dataset.href || a.getAttribute("href");
    if (!href || !href.startsWith("/") || a.target === "_blank" || a.hasAttribute("download")) return;
    e.preventDefault();
    navigate(href);
  });
  return resolve();
}
