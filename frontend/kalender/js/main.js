import { setBase } from "./api.js";
import { mount } from "./app.js";
import * as db from "./db.js";
import * as repo from "./repo.js";
import * as outbox from "./outbox.js";
import * as sync from "./sync.js";
import { S, send } from "./store.js";
import * as update from "./update.js";

const base = document.querySelector('meta[name="kal-base"]')?.content || "";
setBase(base + "/api/v1");
mount(document.getElementById("app"), { pathBase: base, embedded: new URLSearchParams(location.search).has("eingebettet") });

// Hinter dem IDA-Proxy (/kal) gibt es keinen eigenen Service Worker – der gehört der Kalender-App unter eigener Adresse.
if (!base) addEventListener("load", () => update.start());

// Test-Hook für die Browser-Tests: nur sichtbar, wenn localStorage.kalTest === "1"
if (localStorage.getItem("kalTest") === "1") {
  window.__kal = { outboxCount: async () => (await outbox.all()).length, outbox: () => outbox.all(), syncNow: () => sync.syncNow(), push: () => sync.push(), resolve: (seq, c) => sync.resolve(seq, c), retry: (seq) => sync.retry(seq), write: (path, opts) => send(path, opts), net: () => S.net, mirrored: () => repo.windowInfo().then(Boolean), stored: () => db.count("occ"), storage: () => ({ ...db.state }), version: document.querySelector('script[src*="/s/"]')?.src.match(/\/s\/([^/]+)\//)?.[1] || "" };
}
