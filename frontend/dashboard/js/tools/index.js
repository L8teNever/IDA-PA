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
import monitor from "./monitor.js";
import storage from "./storage.js";

export const TOOLS = [overview, stacks, monitor, storage];
