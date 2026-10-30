# IDA Kalender offline – Arbeitspakete im Detail

Gehört zu `docs/KALENDER-OFFLINE.md` (das *Was* und *Warum*). Hier steht das *Wie*: jedes Paket mit Dateien,
konkreten Schritten, Datenformaten, Abnahme-Kriterien und Tests. Reihenfolge = Reihenfolge der Umsetzung.
Jedes Paket ist für sich lauffähig und wird einzeln committet und ausgerollt.

Abkürzungen: **K** = Kalender-Dienst (`kalender/app/…`), **F** = Kalender-Oberfläche (`frontend/kalender/…`),
**I** = IDA (`backend/app/…`), **T** = Tests.

Gesamtübersicht:

| Nr. | Paket | Teil | Phase |
|---|---|---|---|
| 0 | Test-Gerüst (Node-Tests, Playwright, CI-Jobs) | T | vorab |
| 1 | IndexedDB neu: Schema v2 mit Migrationen | F | 1 |
| 2 | Dauerhafter Speicher + Speicheranzeige | F | 1 |
| 3 | `api.js`: Netzfehler, Access-Ablauf, Server-Fehler sauber unterscheiden | F | 1 |
| 4 | Service-Worker-Updates sicher | F | 1 |
| 5 | `sequence` überall + `meta.api` | K | 2 |
| 6 | Endpunkt `GET /api/v1/sync` (Delta) | K | 2 |
| 7 | Lokaler Speicher als einzige Lesequelle (`repo.js`) | F | 2 |
| 8 | Sync-Lesen (Nachladen, Zeitraum, Live-Stream) | F | 2 |
| 9 | Idempotenz (Tabelle + Header) | K | 3 |
| 10 | POST mit vorhandener `uid`, DELETE mit `sequence` | K | 3 |
| 11 | Outbox (Protokoll, Zusammenfassen, lokale IDs) | F | 3 |
| 12 | Optimistische Vorschau | F | 3 |
| 13 | Sync-Senden (Sperre, Backoff, Fehlerarten, ID-Umschreiben) | F | 3 |
| 14 | Konflikte (Sheet „Meine / Server-Version“, Zusammenführen) | F | 3 |
| 15 | Background Sync im Service Worker | F | 3 |
| 16 | Oberfläche: Status-Leiste, Badges, „Abgleich“-Ansicht | F | 3 |
| 17 | Offline-Hinweise an Online-Funktionen | F | 4 |
| 18 | Lokale Erinnerungen bei offener App | F | 4 |
| 19 | Anhänge offline | F/K | 4 |
| 20 | Upgrade-Test gegen das veröffentlichte Image | T | 4 |
| 21 | Doku, Aufräumen, Ausrollen | – | 4 |

---

## Paket 0 – Test-Gerüst (vor allem anderen)

**Ziel:** Jede spätere Änderung wird automatisch auf „offline funktioniert noch“ geprüft.

1. **Node-Tests ohne Abhängigkeiten** (`frontend/kalender/test/*.test.mjs`, Start mit `node --test frontend/kalender/test`):
   * Die reine Logik (Outbox, Vorschau, Zusammenfassen) liegt in Modulen **ohne DOM-Zugriff**, damit Node sie laden kann.
   * IndexedDB in Node: kleines In-Memory-Ersatzobjekt mit derselben Schnittstelle wie `db.js` (siehe Paket 1) –
     kein `fake-indexeddb`-Paket nötig.
2. **Playwright** (`kalender/e2e/`, Python, weil Python schon im Projekt ist):
   * `conftest.py`: startet den echten Kalender-Dienst wie `kalender/tests/conftest.py` (freier Port, Temp-Ordner),
     **aber mit Frontend** (`FRONTEND_DIR` auf `frontend/`), dazu Chromium mit Handy-Viewport (412×915, `has_touch`).
   * Hilfen: `offline(page)` / `online(page)` über `context.set_offline`, `api(...)` für Prüfungen direkt am Server,
     `wait_synced(page)` (wartet bis `window.__kal.outboxCount() === 0`).
   * Test-Hook in der App: `window.__kal = { outboxCount, syncNow, version }` – nur gesetzt, wenn
     `localStorage.kalTest === "1"` (in Produktion unsichtbar).
3. **CI** (`.github/workflows/docker-publish.yml`):
   * Job `kalender-frontend-test`: `node --test`.
   * Job `kalender-e2e`: `pip install -r kalender/requirements-dev.txt playwright` + `python -m playwright install --with-deps chromium`,
     dann `pytest kalender/e2e`. Screenshots bei Fehler als Artefakt hochladen.
   * `image` braucht zusätzlich `needs: [kalender-frontend-test, kalender-e2e]`.
4. **Erster E2E-Test (Ist-Zustand):** Seite laden → offline → neu laden → Kopfzeile und Monat sind da.

**Abnahme:** CI läuft grün mit den neuen Jobs; ein absichtlich kaputter SW lässt den E2E-Test rot werden.

---

## Paket 1 – IndexedDB neu: Schema v2 mit Migrationen (F)

**Neue Datei `js/db.js`** (ersetzt `kvGet/kvSet` in `store.js`):

```js
export const SCHEMA = 2;
export async function open()                       // öffnet "ida-kalender" mit Version SCHEMA
export async function get(store, key) / put(store, value, key?) / del(store, key)
export async function all(store, { index, range } = {})
export async function tx(stores, mode, fn)         // mehrere Stores atomar
```

**Stores (v2):**

| Store | keyPath | Indizes |
|---|---|---|
| `occ` | `id` | `day` (Starttag JJJJ-MM-TT), `event_id`, `end` |
| `events` | `event_id` | – |
| `outbox` | `seq` (autoIncrement) | `target`, `state` |
| `meta` | (externe Schlüssel) | – |
| `kv` | bleibt aus v1 (Fenster-Spiegel), wird in v3 entfernt | – |

**Migrationen** in `onupgradeneeded` als Liste `MIGRATIONS[n] = (db, tx) => {…}`, Schritt für Schritt von `oldVersion` bis `SCHEMA`:
* v1→v2: Stores anlegen; Inhalt von `kv/window` nach `occ` kopieren (Tag aus `start` ableiten);
  `kv/cals` und `kv/settings` nach `meta`.
* **localStorage `kal-queue`** (alte Warteschlange) nach dem Öffnen einmalig in `outbox` umschreiben
  (`{path, method, body}` → `type` aus Methode/Pfad, `opId = crypto.randomUUID()`), dann `kal-queue` löschen.
  Gelingt das nicht, bleibt `kal-queue` stehen (lieber doppelt versuchen als verlieren).
* Regel für alle künftigen Migrationen: **outbox nie löschen**, nur umformen; Caches (`occ`, `events`) dürfen geleert werden.

**Fehlerfälle:** IndexedDB nicht verfügbar (privater Modus, alter Browser) → App läuft wie heute rein online,
Hinweis „Offline-Speicher nicht verfügbar“ in den Einstellungen.

**T:** Node-Test für die Migrationsfunktionen mit dem In-Memory-Ersatz; E2E „Schema-Test“: v1-Daten + `kal-queue`
per `page.evaluate` vorbefüllen, neu laden → Termine sichtbar, Outbox enthält den alten Eintrag.

**Abnahme:** Bestehende Handys mit v1-Daten verlieren nichts; `store.js` benutzt nur noch `db.js`.

---

## Paket 2 – Dauerhafter Speicher (F)

* Beim ersten Schreiben in die Outbox und beim Installieren: `navigator.storage.persist()`.
* Einstellungen → Daten: „Offline-Speicher: 2,3 MB von 1,2 GB · dauerhaft ✓/✗“ (`navigator.storage.estimate()`, `persisted()`).
* Ist „dauerhaft ✗“ und es warten Änderungen: Hinweis „Bitte die App installieren, damit das Handy die Daten behält“.

**Abnahme:** Wert in den Einstellungen sichtbar; in Chrome nach Installation `persisted() === true`.

---

## Paket 3 – `api.js`: Fehler sauber unterscheiden (F)

Heute: jeder `fetch`-Fehler gilt als „offline“. Neu eine Fehlerklasse mit `kind`:

| `kind` | Woran erkannt | Folge |
|---|---|---|
| `offline` | `fetch` wirft (TypeError) **und** `navigator.onLine === false` oder Timeout (10 s via `AbortController`) | Outbox wartet |
| `auth` | `redirect: "manual"` → `res.type === "opaqueredirect"`, oder 401/403 mit HTML-Antwort (Cloudflare Access) | Leiste „Bitte neu anmelden“ |
| `server` | 5xx, 429, oder JSON nicht lesbar | Wiederholen mit Backoff |
| `conflict` | 409 | Konflikt-Sheet |
| `gone` | 404 bei Termin-Endpunkten | Konflikt „wurde gelöscht“ |
| `invalid` | 400/403/422 mit JSON `detail` | Fehler anzeigen, Eintrag bleibt |

* `fetch(..., { redirect: "manual", credentials: "same-origin", signal })`.
* `fetch` wirft, obwohl `navigator.onLine === true`: einmal `GET /api/v1/health` mit `redirect: "manual"` – kommt
  `opaqueredirect`, ist es `auth`, sonst `offline`.
* SSE (`live`): bei `auth` nicht endlos neu verbinden, sondern auf „neu anmelden“ warten.
* Neuer Zustand im Store: `S.net = "online" | "offline" | "auth" | "syncing"` statt `S.offline`.

**T:** E2E: Route `**/api/v1/**` auf `302 Location: https://example.cloudflareaccess.com/…` umbiegen →
Leiste „Bitte neu anmelden“, keine Offline-Leiste, Outbox unverändert. Node-Test für die Klassifizierung.

**Abnahme:** Abgelaufene Access-Sitzung wird nie als „offline“ angezeigt.

---

## Paket 4 – Service-Worker-Updates sicher (F)

`sw.js`:
1. `install`: Hülle cachen, **kein** `skipWaiting()` mehr.
2. `activate`: alte Caches nur löschen, wenn `clients.matchAll()` kein Fenster mit alter Version mehr hat
   (Version steckt im Pfad `/s/<v>/` der geladenen Skripte → Fenster melden ihre Version per `postMessage`).
   Sonst Aufräumen beim nächsten `activate`.
3. Nachricht `{type: "skip"}` von der Seite → `skipWaiting()`.
4. Navigation offline: Fallback auf `"/"` aus dem Cache (gibt es) – zusätzlich für jeden Pfad (`/monat/…`, `/termin/…`).

`main.js`:
1. `registration.onupdatefound` → neuer SW `installed` und es gibt schon einen Controller → Snackbar „Neue Version da · Neu laden“.
2. „Neu laden“: wenn Outbox leer → `skip` senden, bei `controllerchange` `location.reload()`.
   Outbox nicht leer und online → erst `syncNow()`, dann wie oben. Offline → „Wird nach dem Abgleich aktualisiert“.
3. Kein Fenster offen → neuer SW wird beim nächsten Start ohnehin aktiv (Standardverhalten).
4. `GET /api/v1/meta` → `min_client` (Paket 5) größer als eigene Version → Update erzwingen (nach Abgleich).

**T:** E2E „Update-Test“: Version A laden (Server mit Frontend-Kopie in Temp-Ordner), offline Termin anlegen,
eine Datei im Temp-Frontend ändern + Server neu starten (neue Version B), online → Snackbar erscheint,
„Neu laden“ → Outbox wurde vorher gesendet, Version B aktiv, Cache A gelöscht.

**Abnahme:** Ein Update während offener App zerstört nichts und verliert keine Änderung.

---

## Paket 5 – `sequence` überall + `meta.api` (K)

* `services/query.py` → `occ_view`: Feld `sequence` (des Termins bzw. bei Ausnahmen der Ausnahme) und `updated_at`.
* `api/misc.py` → `/meta`: `"api": 2`, `"min_client": ""` (leer = alle erlaubt; später Version setzen, falls nötig).
* `api/events.py` → PATCH: Prüfung auf `sequence` gibt es schon; Antwort `409` bekommt im JSON zusätzlich
  `"server": <aktuelle Detail-Ansicht>`, damit die App den Konflikt ohne weiteren Request zeigen kann.
  (Dafür `errors.Conflict` um ein optionales `extra` erweitern und im Exception-Handler mitsenden.)

**T (pytest):** `sequence` steigt bei jedem PATCH; 409 enthält `server`; `/meta.api == 2`.

---

## Paket 6 – Endpunkt `GET /api/v1/sync` (K)

**Anfrage:** `?since=<change_id>&from=JJJJ-MM-TT&to=JJJJ-MM-TT` (`since` fehlt = erster Abgleich).

**Antwort:**
```json
{ "cursor": 1234, "full": false,
  "upserts": [ …Vorkommen wie /events, nur der geänderten Termine, im Zeitraum… ],
  "removed_events": ["abc123", …],        // gelöscht oder ins nicht-lesbare verschoben
  "calendars": [ … ] | null,              // nur wenn sich Kalender geändert haben
  "settings": { … } | null }
```

Umsetzung (`api/misc.py` oder neues `api/sync.py`):
1. `cursor` = höchste `Change.id`.
2. `since` fehlt oder `since < kleinste noch vorhandene Change.id` (Verlauf nach 90 Tagen gelöscht) → `full: true`,
   `upserts` = alle Vorkommen im Zeitraum (wie `/events`), `removed_events = []`.
3. Sonst: alle `Change` mit `id > since` → Menge der `event_id`s (bei Ausnahmen auch die Eltern-Serie).
   Für jede: Termin laden; gelöscht/fehlt/nicht lesbar → `removed_events`; sonst Vorkommen im Zeitraum
   über `query.raw_occurrences` (nur dieser Termin) → `upserts`. Die App ersetzt **alle** Vorkommen dieser Termine.
4. Kalender/Einstellungen: zusätzlich Zeitstempel `calendars_changed_at` / `settings_changed_at` in `settings`
   (bei jedem Ändern setzen), App schickt `&meta_at=` mit; neuer → mitsenden.
5. Rechte: wie `/events` (Agents nur lesbare Kalender).
6. Grenzen: Zeitraum höchstens 25 Monate; mehr als 2000 Termine geändert → `full: true`.

**T (pytest, `kalender/tests/test_sync.py`):** erster Abgleich voll; nach create/update/delete nur Delta;
Serie ändern (`all`, `this`, `following`) → richtige Vorkommen; Cursor zu alt → `full`; Agent ohne Leserecht
sieht den Termin als entfernt; Kalender umbenannt → `calendars` gesetzt.

---

## Paket 7 – Lokaler Speicher als einzige Lesequelle (F)

**Neue Datei `js/repo.js`** – alles, was die Ansichten lesen:

```js
export async function range(from, to)        // Vorkommen aus occ (Index day), + Vorschau aus der Outbox (Paket 12)
export async function event(id)              // Details aus events, fehlt → Server, dann speichern
export async function calendars() / settings()
export function onChange(fn)                 // wird nach jedem Abgleich/jeder lokalen Änderung aufgerufen
```

* `store.js`: `loadEvents()` liest nur noch `repo.range(...)` (schnell, ohne Netz) und stößt danach `sync.pull()` an.
  `render()` zeichnet also sofort aus dem Speicher, der Abgleich ruft `onChange` → neu zeichnen (ohne Flackern,
  Scroll-Position bleibt – gibt es schon über `paint(quiet)`).
* `openDetail` (`sheets.js`): erst `repo.event(id)`, Server nur, wenn nicht vorhanden; offline mit Hinweis
  „Stand 14:02“.
* Suche offline (`offlineSearch` in `sheets.js`) liest `occ` statt `kv/window`.
* `views.js` bleibt unverändert (bekommt weiter `S.events`).

**Abnahme:** Mit gezogenem Netzkabel blättert man durch −3 bis +12 Monate, öffnet Details, sucht – alles geht.

---

## Paket 8 – Sync-Lesen (F)

**Neue Datei `js/sync.js`** (Lese-Teil):

```js
export async function pull({ full = false } = {})   // GET /sync?since=cursor&from&to → occ/events/meta aktualisieren
export function start()                             // Auslöser registrieren
```

* Zeitraum: `from = heute − 3 Monate`, `to = heute + 12 Monate`. Blättert der Nutzer darüber hinaus und ist online:
  einmal `/events` für den neuen Monat laden, in `occ` legen, Zeitraum in `meta` erweitern (max. 25 Monate,
  ältestes fällt beim nächsten `full` raus).
* Anwenden in **einer** Transaktion: für jede `event_id` in `upserts`/`removed_events` alle `occ` mit diesem
  `event_id` löschen, dann `upserts` schreiben; `events` (Details) für diese IDs löschen (werden bei Bedarf neu geholt).
  **Ausnahme:** Termine mit offenem Outbox-Eintrag werden nicht angefasst (Vorschau bleibt, bis gesendet).
* Auslöser: App-Start, `online`, `visibilitychange` → sichtbar, SSE-Nachricht `changed` (statt heute `render(true)`),
  SSE neu verbunden, alle 5 Minuten wenn sichtbar.
* Feiertage: kommen über `/events` mit; bei `full` mitgeladen (sie haben keine Änderungen im Verlauf –
  einmal pro Tag `full` für Feiertage, oder Feiertage clientseitig nicht cachen → **Entscheidung:** einmal täglich voll).
* `S.loadedAt` = Zeit des letzten erfolgreichen `pull` → Leiste „Stand 14:02“.

**T:** E2E: Online laden, per API (als Ida) Termin ändern → erscheint ohne Neuladen (SSE → pull);
offline, per API ändern, online → nach `online` sichtbar; Node-Test für das Anwenden inkl. „Outbox schützt Vorschau“.

---

## Paket 9 – Idempotenz (K)

* **Migration `alembic/versions/0002_requests.py`:** Tabelle
  `requests(key String(64) PK, method String(8), path String(200), status Integer, response JSON, at DateTime)`, Index auf `at`.
* `models.py`: Klasse `Request`.
* Neue Abhängigkeit/Funktion in `api/events.py` (für POST/PATCH/DELETE `/events…`, `restore`):
  1. Header `Idempotency-Key` fehlt → Verhalten wie heute.
  2. Vorhanden → in derselben Schreib-Transaktion nachsehen: gibt es den Key → gespeicherte Antwort mit
     gespeichertem Status zurück (Header `Idempotent-Replay: 1`). Gleicher Key, anderer Pfad/Methode → 422.
  3. Sonst Aktion ausführen und Antwort + Status speichern (**in derselben Transaktion** wie die Änderung –
     entweder beides oder nichts).
  4. Fehlerantworten (4xx) werden ebenfalls gespeichert (gleiche Wiederholung → gleicher Fehler), 5xx nicht.
* `scheduler.maintenance()`: Einträge älter als 14 Tage löschen.
* IDA-Proxy (`backend/app/api/kalender.py`): Header `idempotency-key` in die Liste der durchgereichten Header aufnehmen.

**T (pytest):** zweimal POST mit gleichem Key → ein Termin, gleiche ID; PATCH zweimal → `sequence` nur +1;
DELETE zweimal → zweites Mal gleiche Antwort statt 404; anderer Pfad mit gleichem Key → 422;
Key wird nach der Pflege gelöscht. **T (IDA):** Proxy reicht den Header weiter.

---

## Paket 10 – POST mit vorhandener `uid`, DELETE mit `sequence` (K)

* `store.create_event`: gibt es einen (nicht gelöschten) Termin mit dieser `uid` → diesen zurückgeben,
  `existing: True` in der API-Antwort, kein neuer Verlaufseintrag. Gelöscht im Papierkorb → wiederherstellen? **Nein**
  – als `existing` mit `deleted: true` melden, die App zeigt den Konflikt „wurde gelöscht“.
* `DELETE /events/{occ}?scope=&sequence=` → wie PATCH: 409 mit `server`, wenn geändert.
* `restore` braucht nichts.

**T (pytest):** POST gleiche `uid` → `existing`; DELETE mit alter `sequence` → 409; mit aktueller → ok.

---

## Paket 11 – Outbox (F)

**Neue Datei `js/outbox.js`** – reine Logik + Speicher, kein DOM:

```js
export async function add(op)            // {type, target, occ, scope, body, baseSequence}; vergibt opId, seq, createdAt, v:1
export async function pending()          // sortiert nach seq, ohne state "conflict"/"error" blockierte Termine
export async function mark(seq, patch)   // state/tries/error setzen
export async function remove(seq)
export async function remap(localId, realId, realOcc)  // local_… → echte IDs in allen Einträgen
export function coalesce(ops)            // reine Funktion, Regeln unten
export const count = () => …
```

**Lokale IDs:** create offline → `event_id = "local_" + uuid`, Vorkommen-ID `local_<uuid>` (bei Serie
`local_<uuid>_<JJJJMMTTTHHMM>`), `body.uid = "<uuid>@ida-kalender"` (Paket 10 macht Wiederholen sicher).

**Zusammenfassen (`coalesce`, nur Einträge im Zustand `pending`, nie `sending`):**

| vorher | danach kommt | Ergebnis |
|---|---|---|
| create | update (gleiches Ziel) | create mit zusammengeführtem `body` |
| create | delete | beide weg |
| update(scope s) | update(scope s, gleiches Vorkommen) | ein update, `body` zusammengeführt, `baseSequence` des ersten |
| update | delete | delete mit `baseSequence` des updates |
| delete | irgendwas | nicht möglich (UI verhindert Bearbeiten gelöschter) |

Unterschiedliche `scope`s auf dieselbe Serie werden **nicht** zusammengefasst (Reihenfolge bleibt).

**Schreiben aus der UI:** `send()` in `store.js` wird ersetzt durch `write(op)`:
1. `outbox.add(op)` → 2. `preview.apply(op)` (Paket 12) → 3. neu zeichnen → 4. `sync.push()` anstoßen (falls online).
Die UI wartet **nie** auf den Server; die Snackbar sagt „Gespeichert“ sofort, „Rückgängig“ arbeitet lokal
(Eintrag noch `pending` → einfach entfernen + Vorschau zurücknehmen; schon gesendet → wie heute `POST /changes/{id}/undo`).

Betroffene Stellen: `sheets.js` (`openEditor.save`, `deleteEvent`, `patchEvent`, Checklisten-Haken, Schnelleingabe,
Papierkorb-Wiederherstellen), `app.js` (Ziehen im Raster, Wischen, Farbe per Rechtsklick).

**T (Node):** alle Zeilen der Tabelle; Reihenfolge; `remap`; 1000 Einträge performant.

---

## Paket 12 – Optimistische Vorschau (F)

**Neue Datei `js/preview.js`** (reine Logik):

```js
export function apply(occList, op, eventsById)   // → neue occ-Liste
```

* create: Vorkommen bauen wie `occ_view` (Farbe aus Kalender, `_pending: true`). Serie: einfache Regeln
  (`daily`, `weekdays`, `weekly` + Tage, `monthly` am Tag, `yearly`, `interval`, `count`, `until`) im Zeitraum selbst
  auflösen; alles andere: nur erstes Vorkommen + `_partial: true` („Serie wird nach dem Abgleich vollständig angezeigt“).
* update scope=this: Felder/Zeiten am Vorkommen ändern. scope=all/following: Zeitdifferenz und Felder auf alle bzw.
  ab diesem Vorkommen anwenden; Wiederholungsregel geändert → wie create der neuen Regel + alte Vorkommen ausblenden.
* delete: `_pending: "delete"` (ausgeblendet; Papierkorb zeigt sie lokal mit Wolke).
* `repo.range()` = gespeicherte `occ` + `apply` aller offenen Outbox-Einträge in Reihenfolge → so überlebt die
  Vorschau Neuladen und App-Neustart, ohne doppelt gespeichert zu werden.

**T (Node):** jede Variante; Serie ändern; Vorschau nach Neustart identisch.

---

## Paket 13 – Sync-Senden (F)

In `js/sync.js`:

```js
export async function push()   // sendet die Outbox der Reihe nach
export async function syncNow() // push(), dann pull()
```

1. **Sperre:** `navigator.locks.request("kal-sync", { ifAvailable: true }, …)`; kein Lock-API → Zeitstempel-Flag in `meta`
   (älter als 60 s = frei).
2. Vor dem Senden: `coalesce` auf die wartenden Einträge anwenden und zurückschreiben.
3. Eintrag für Eintrag: `state = "sending"` → Request mit `Idempotency-Key: opId`, bei update/delete `sequence = baseSequence`
   (bei Zielen, die noch `local_…` sind und deren create gerade gesendet wurde: vorher `remap`).
4. Ergebnis nach Fehlerart (Paket 3):
   * ok → `remove`; create: `remap(local, echt)`; Server-Antwort (`event`) in `events` und – über einen kleinen
     `pull` nur für diesen Termin – in `occ`.
   * `offline`/`server` → `state = "pending"`, `tries++`, `nextTry` = 5 s · 3^tries (max. 15 min), Schleife abbrechen.
   * `auth` → abbrechen, `S.net = "auth"`.
   * `conflict`/`gone` → `state = "conflict"`, Server-Stand aus der 409-Antwort speichern, mit nächstem Eintrag
     (anderer Termin) weitermachen.
   * `invalid` → `state = "error"`, `error = detail`, weiter.
   * `sending` beim App-Start vorgefunden (abgestürzt) → wie `pending` behandeln (Idempotenz macht es sicher).
5. Nach dem Senden: `pull()`; Snackbar „3 Offline-Änderungen gespeichert“ (nur wenn vorher offline gewartet wurde).
6. Auslöser wie Paket 8 + direkt nach jedem `write()` + Timer für `nextTry`.

**T (E2E):**
* offline: anlegen, bearbeiten, verschieben, anderen löschen → online → Server-Stand stimmt, keine Doppelten, Wolken weg.
* Abbruch während des Sendens: `page.route` lässt den POST zum Server durch, liefert dem Browser aber einen Fehler →
  erneut gesendet → genau ein Termin.
* offline anlegen + offline löschen → Server sieht nie etwas (Request-Log der Route prüfen).
* Reihenfolge: anlegen, dann verschieben → Server hat die verschobene Zeit.

---

## Paket 14 – Konflikte (F)

* `sheets.js` → `openConflict(entry)`: Bottom-Sheet „Termin wurde inzwischen geändert“ (bzw. „gelöscht“).
  * Tabelle nur der unterschiedlichen Felder: Titel, Zeit, Ort, Kalender, Beschreibung, Erinnerungen, Wiederholung –
    „Meine Version“ links, „Jetzt“ rechts, Änderer („von Ida · vor 10 Min.“ aus `/changes?event_id=`).
  * Knöpfe: **Meine übernehmen** (neuer Eintrag mit `baseSequence` = Server-`sequence`, alter wird entfernt) ·
    **Server behalten** (Eintrag verwerfen, Vorschau weg) · bei „gelöscht“: **Neu anlegen** / **Verwerfen**.
* Automatisch zusammenführen ohne Nachfrage, wenn sich **verschiedene** Felder geändert haben (meine Felder vs.
  Server-Änderung laut `/changes`-Diff `before/after`) → neuer Eintrag mit aktueller `sequence`, Snackbar
  „Mit Idas Änderung zusammengeführt“.
* Konflikte erscheinen: rotes Badge am Termin, oben in der „Abgleich“-Ansicht, Snackbar „1 Konflikt – Ansehen“.

**T (E2E):** offline Titel ändern, per API (als Ida) Zeit ändern, online → automatisch zusammengeführt (beides da);
offline Zeit ändern, per API Zeit ändern → Konflikt-Sheet, beide Knöpfe; offline ändern, per API löschen → „gelöscht“.

---

## Paket 15 – Background Sync (F)

* `main.js`/`sync.js`: nach `write()` offline → `registration.sync.register("kal-outbox")` (falls vorhanden).
* `sw.js`: `self.addEventListener("sync", e => e.tag === "kal-outbox" && e.waitUntil(pushFromSw()))`.
  `pushFromSw` importiert dieselben Module (`importScripts` geht bei ES-Modulen nicht → SW als **Modul-SW**
  registrieren: `register("/sw.js", { type: "module" })`, Version im SW über `__VERSION__` wie heute).
  Sperre (Paket 13) verhindert Doppelsenden mit offenem Fenster.
* Nach Erfolg: Fenster benachrichtigen (`clients.matchAll` → `postMessage({type:"synced"})`).
* iPhone (kein Background Sync): Einstellungen erklären „wird beim nächsten Öffnen gesendet“.

**T:** Chromium unterstützt `sync` in Playwright nur eingeschränkt → Test ruft den Handler direkt über
`page.evaluate(() => navigator.serviceWorker.controller.postMessage({type:"test-sync"}))` auf (Test-Hook nur bei `kalTest`).

---

## Paket 16 – Oberfläche (F)

* **Status-Leiste** unter der Top-Bar (ersetzt `#offline`), Zustände aus `S.net` + Outbox:
  * offline: „Offline – Stand 14:02 · 3 Änderungen warten“
  * syncing: dünner Fortschrittsbalken im Akzent, kein Text
  * auth: „Anmeldung abgelaufen · 3 Änderungen warten · [Anmelden]“
  * conflict: „1 Konflikt · [Ansehen]“
* **Badges:** Wolke (`cloud_upload`) an Karten **und** Raster-Blöcken, Pillen (kleiner Punkt), Detail-Sheet
  („Noch nicht gespeichert – wird gesendet, sobald du online bist“); Konflikt = rotes `sync_problem`.
* **„Abgleich“-Ansicht** (Drawer → Mehr → Abgleich, auch über die Leiste): Liste der Outbox-Einträge mit Typ,
  Titel, Zeitpunkt, Zustand; pro Eintrag „Erneut senden“ / „Verwerfen“ (mit Bestätigung); oben „Jetzt abgleichen“,
  „Zuletzt abgeglichen 14:02“, Speicher (Paket 2), Hinweis iPhone/Background Sync.
* **App-Badge** (`navigator.setAppBadge(n)`) mit der Anzahl wartender Änderungen, wo unterstützt.

**T (E2E):** Texte der Leiste in allen Zuständen; „Verwerfen“ entfernt Eintrag und Vorschau.

---

## Paket 17 – Offline-Hinweise an Online-Funktionen (F)

Ausgegraut mit Untertitel „braucht Internet“, solange `S.net !== "online"`: Ida fragen, Schnelleingabe-Vorschau
(Parser ist serverseitig – **offline:** Text als Titel übernehmen und Editor öffnen), Abo-Link/QR, Backups,
Import/Export, Push an/aus, Anhänge (bis Paket 19), Verlauf/Papierkorb (zeigen lokalen Stand).

---

## Paket 18 – Lokale Erinnerungen bei offener App (F)

* Solange die App offen ist: jede Minute `occ` der nächsten 24 h prüfen (`alarms` je Vorkommen), fällige zeigen
  (`showAlarm`, gibt es) – gemerkt in `meta.alarmsShown` (Schlüssel `event_id|occ|minutes`), damit Server-SSE und
  lokale Prüfung nicht doppelt klingeln.
* Online kommt die Erinnerung weiterhin vom Server (SSE/Push); lokal nur, wenn `S.net !== "online"`.

---

## Paket 19 – Anhänge offline (F/K)

* Offline angehängte Datei → Store `files` (Blob, `opId`, `target`, Name, Größe), Outbox-Eintrag `type: "upload"`
  (nach dem create des Termins einsortiert, `remap` gilt auch hier).
* Senden: `POST /events/{id}/files` mit `Idempotency-Key` (K: Upload-Endpunkt in Paket 9 einschließen).
* Detail/Editor zeigen lokale Anhänge mit Wolke; Öffnen über `URL.createObjectURL`.
* Grenze: 20 MB je Datei, 100 MB gesamt offline; darüber Hinweis.

---

## Paket 20 – Upgrade-Test gegen das veröffentlichte Image (T)

CI-Job `kalender-upgrade` (nur auf `main`):
1. `ghcr.io/<owner>/ida-kalender:latest` (= aktuell ausgerollt) mit Volume starten, per API Kalender, Serie, Ausnahme,
   Papierkorb-Termin, Abo-Token, Push-Abo, Anhang anlegen.
2. Container stoppen, **neu gebautes Image** auf demselben Volume starten.
3. Prüfen: Migration gelaufen (`/health`), alle Daten da, `/sync` liefert vollen Stand, alter Abo-Link geht noch.
4. Zusätzlich E2E: Browser mit der **alten** Oberfläche + Outbox-Einträgen im alten Format gegen den **neuen** Server
   → werden angenommen (Abwärtskompatibilität der API).

---

## Paket 21 – Doku, Aufräumen, Ausrollen

* `docs/KALENDER.md` §11 und §7.12 auf den neuen Stand; `docs/KALENDER-OFFLINE.md` als „umgesetzt“ markieren.
* Alte Wege entfernen: `kvGet/kvSet`, `kal-queue`, `mirror()`, `cachedEvents()` (nach einer Übergangsversion,
  in der die Migration sicher gelaufen ist → Store `kv` erst in Schema v3 löschen).
* Ausrollen je Phase: push → CI (alle Tests inkl. E2E/Upgrade) → `docker compose pull && up -d` auf dem Server →
  kurzer Rauchtest auf `kalender.kulbarts.com` (offline schalten im Handy, Termin anlegen, wieder online).

---

## Reihenfolge der Commits (Vorschlag)

1. P0 Test-Gerüst · 2. P1+P2 Speicher · 3. P3 Fehlerarten · 4. P4 SW-Updates → **ausrollen (Phase 1)**
5. P5+P6 Backend Sync · 6. P7+P8 lokal lesen → **ausrollen (Phase 2)**
7. P9+P10 Backend Idempotenz · 8. P11+P12 Outbox/Vorschau · 9. P13 Senden · 10. P14 Konflikte · 11. P15+P16 → **ausrollen (Phase 3)**
12. P17–P19 · 13. P20 · 14. P21 → **ausrollen (Phase 4)**

## Offene Entscheidungen (mit Vorschlag)

| Frage | Vorschlag |
|---|---|
| Zeitraum offline | −3 / +12 Monate (≈ 1–3 MB) |
| Feiertage offline | einmal täglich voll mitladen |
| Automatisch zusammenführen bei Konflikt | ja, wenn verschiedene Felder; sonst fragen |
| „Erledigt“-Haken (heute nur lokal) | später als echtes Feld im Termin (eigene Mini-Migration), damit er synchronisiert wird |
| Ausnahme bei Serien offline | korrekt speichern, Anzeige angenähert (Hinweis am Termin) |
