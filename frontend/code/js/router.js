// Adressen der App:  /  ·  /p/:project  ·  /p/:project/neu  ·  /p/:project/t/:thread
let handler = () => {};

export function parse(path = location.pathname) {
  const m = /^\/p\/([^/]+)(?:\/(neu|t\/([^/]+)))?\/?$/.exec(path);
  if (!m) return { view: "home" };
  const project = decodeURIComponent(m[1]);
  if (m[2] === "neu") return { view: "thread", project, thread: null };
  if (m[3]) return { view: "thread", project, thread: decodeURIComponent(m[3]) };
  return { view: "project", project };
}

export const urlOf = ({ project, thread } = {}) => (!project ? "/" : thread === null ? `/p/${project}/neu` : thread ? `/p/${project}/t/${thread}` : `/p/${project}`);

export function navigate(path, { replace = false } = {}) {
  if (path === location.pathname + location.search) return handler(parse(path));
  history[replace ? "replaceState" : "pushState"](null, "", path);
  handler(parse(path));
}

// Adresse ändern, ohne neu zu zeichnen (z. B. /p/x/neu -> /p/x/t/<id>, sobald der Thread angelegt ist)
export function setUrl(path) {
  if (path !== location.pathname) history.replaceState(null, "", path);
}

export function startRouter(fn) {
  handler = fn;
  addEventListener("popstate", () => handler(parse()));
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download") || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname.startsWith("/s/")) return;
    e.preventDefault();
    navigate(u.pathname + u.search);
  });
  handler(parse());
}
