// Alle Tools des Dashboards. Neues Tool: Datei in diesem Ordner anlegen (gleicher Aufbau wie die anderen) und hier eintragen.
//
// Aufbau eines Tools:
//   { id, label, icon,            – Leiste links / unten
//     path,                       – Hauptadresse, z. B. "/stacks"
//     match(path) -> params|null, – welche Adressen zum Tool gehören
//     side?(box, params, ctx),    – optional: Inhalt der Seitenleiste (sonst ist sie weg)
//     mount(stage, params, ctx)   – Inhalt zeichnen; gibt eine Aufräum-Funktion zurück (Timer, Abos …)
//   }
// ctx: { top(html) – Kopfzeile setzen, scroll – Inhaltsbereich, navigate(url), openDrawer() }
import overview from "./overview.js";
import stacks from "./stacks.js";
import files from "./files.js";
import monitor from "./monitor.js";
import storage from "./storage.js";
import sites from "./sites.js";
import publish from "./publish.js";
import graph from "./graph.js";
import ports from "./ports.js";
import links from "./links.js";
import updates from "./updates.js";

// Dieselbe Oberfläche läuft als IDA Dashboard (Überblick) und als IDA Server (einrichten). Welche Tools in welcher
// Reihenfolge in der Leiste stehen, sagt der Server (/api/v1/app → nav); ohne Antwort gilt die Liste des Dashboards.
export const ALL = { overview, stacks, graph, ports, files, monitor, storage, sites, publish, links, updates };
export const DASHBOARD_NAV = ["overview", "stacks", "graph", "publish", "ports", "files", "monitor", "storage"];
export const TOOLS = [];
export function setTools(nav) {
  TOOLS.length = 0;
  for (const id of nav?.length ? nav : DASHBOARD_NAV) if (ALL[id]) TOOLS.push(ALL[id]);
}
setTools(DASHBOARD_NAV);
