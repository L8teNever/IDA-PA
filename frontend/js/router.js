// Small history-API router: every page has a real URL (/chats, /einstellungen/agents/lea …).
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

export function navigate(url, { replace = false } = {}) {
  if (url === location.pathname + location.search && !replace) return resolve();
  history[replace ? "replaceState" : "pushState"](null, "", url);
  return resolve();
}

// Only changes the URL (e.g. a new chat got its id) without rendering again.
export function setUrl(url) { history.replaceState(null, "", url); current = url; }
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
