# IDA Kalender – Offline lesen und schreiben (Plan)

Ziel: Am Handy (installierte PWA) sind die Termine **immer da**, auch ohne Netz. Man kann offline
**anlegen, ändern, verschieben und löschen**. Sobald wieder Internet da ist, wird alles **automatisch und
ohne Datenverlust** abgeglichen. Das muss auch **nach App-Updates** weiter funktionieren
(offene Änderungen dürfen ein Update nie überleben „müssen“, sondern werden mitgenommen).

Stand: Plan, noch nicht umgesetzt. Ergänzt `docs/KALENDER.md` §11 (PWA) und §7.12 (Zustände).

---

## 1. Was es heute schon gibt – und wo es hakt

| Heute | Problem |
|---|---|
| Service Worker cacht die App-Hülle (`/s/<version>/…`), Seite offline aus dem Cache | Neuer SW macht sofort `skipWaiting` **und löscht den alten Cache**. Eine noch offene alte Seite, die danach ein Modul nachlädt, bricht. Kein „Update bereit“-Hinweis. |
| Spiegel in IndexedDB: **einmal beim Start** ±3 Monate komplett geladen (`window`) | Kein laufender Abgleich; nach einer Woche offline ist der Spiegel veraltet, Serien jenseits von 3 Monaten fehlen ganz. |
| Offline-Warteschlange in **localStorage** (`kal-queue`) | Nicht versioniert; bei einem Fehler, der kein Netzfehler ist (z. B. 409 Konflikt, 400), wird die Änderung **stillschweigend verworfen** → Datenverlust. |
| Offline angelegte Termine | Erscheinen **nicht** im Kalender, bis wieder Netz da ist (kein optimistisches Anzeigen). Ein offline angelegter Termin kann offline nicht weiter bearbeitet werden (er hat noch keine ID). |
| Doppelt senden | Bricht die Verbindung **nach** dem Speichern auf dem Server, aber **vor** der Antwort ab, wird beim Nachholen doppelt angelegt. |
| Cloudflare Access (Login vor `kalender.kulbarts.com`) | Ist die Access-Sitzung abgelaufen (720 h), leitet Cloudflare jede API-Anfrage auf die Login-Seite um. `fetch` wertet das als Netzfehler → die App hält sich für **offline**, die Warteschlange hängt für immer. |
| iPhone/Android dürfen Speicher einer PWA bei Platzmangel löschen | Kein `navigator.storage.persist()` → Spiegel **und Warteschlange** können verschwinden. |
| Backend: `PATCH` kennt schon `sequence` (409, wenn der Termin inzwischen geändert wurde) | `DELETE` hat keinen solchen Schutz, `POST` ist nicht idempotent (doppelte `uid` → Fehler statt „gibt es schon“). |

---

## 2. Grundidee (Architektur)

```
 UI  ──liest──▶  Lokaler Speicher (IndexedDB)  ◀──Abgleich──  Sync-Modul  ◀──▶  Kalender-API
      ──schreibt──▶ Änderungsprotokoll (Outbox) ──────────────┘
```

* **Lokal zuerst:** Die Oberfläche liest **immer** aus IndexedDB. Der Server füllt/aktualisiert IndexedDB im Hintergrund.
  Online und offline sehen also gleich aus – nur die Leiste „Offline – Stand 14:02“ unterscheidet sich.
* **Schreiben = Eintrag ins Änderungsprotokoll** (Outbox) **plus sofort lokal anwenden** (optimistisch).
  Online wird das Protokoll gleich gesendet; offline wartet es.
* **Server bleibt die Wahrheit:** Nach jedem gesendeten Eintrag übernimmt die App die Antwort des Servers
  (echte ID, `sequence`, aufgelöste Serien) und ersetzt die lokale Vorschau.
* **Ein** Sync-Modul (`js/sync.js`) macht alles: senden, nachladen, Konflikte, Wiederholen. Es läuft im Fenster
  und – wo der Browser es kann – zusätzlich im Service Worker (Background Sync).

---

## 3. Lokaler Speicher (IndexedDB `ida-kalender`, mit Schema-Version)

| Store | Inhalt | Schlüssel |
|---|---|---|
| `occ` | aufgelöste Vorkommen wie von `GET /events` (+ Felder `_local`, `_pending`) | `id` (Vorkommen-ID), Index auf `day` (JJJJ-MM-TT) und `event_id` |
| `events` | volle Termin-Details (für Detail-Sheet/Editor offline) | `event_id` |
| `outbox` | Änderungsprotokoll (siehe §4) | `seq` (fortlaufend) |
| `meta` | `schema`, `cursor` (letzte bekannte Änderungs-ID), Spiegel-Zeitraum `from`/`to`, `syncedAt`, Kalender, Einstellungen | Name |
| `files` *(Phase 4)* | offline angehängte Dateien (Blob), bis sie hochgeladen sind | `opId` |

* **Zeitraum:** −3 bis **+12 Monate** (Serien werden vom Server aufgelöst; so sind auch lange Serien offline da).
  Blättert man weiter, wird bei Netz nachgeladen und der Zeitraum erweitert (höchstens 24 Monate, Älteres fliegt raus).
* **Schema-Migrationen:** `onupgradeneeded` mit Schritten v1→v2→v3 … Nie Daten wegwerfen, außer reinem Cache.
  Die **Outbox wird bei jeder Migration übernommen**, notfalls in das neue Format umgeschrieben.
* Die alte localStorage-Warteschlange (`kal-queue`) wird beim ersten Start einmalig in die Outbox übernommen.
* **Dauerhafter Speicher:** `navigator.storage.persist()` beim ersten Schreiben anfragen; in den Einstellungen
  „Offline-Speicher: 2,3 MB · dauerhaft ✓“ anzeigen.

---

## 4. Änderungsprotokoll (Outbox)

Jeder Eintrag:

```json
{ "seq": 17, "v": 1, "opId": "7f3c…(UUID)", "type": "create|update|delete",
  "target": "abc123 | local_9e1…", "occ": "abc123_20261103T0900", "scope": "this|following|all",
  "body": { … }, "baseSequence": 4, "createdAt": "2026-10-30T14:02:11+01:00",
  "tries": 0, "state": "pending|sending|conflict|error", "error": null }
```

* **Offline angelegte Termine** bekommen sofort eine lokale ID `local_<uuid>` **und** eine feste `uid`
  (`<uuid>@ida-kalender`). Damit kann man sie offline weiter bearbeiten/löschen; nach dem Senden wird
  `local_…` überall (occ, events, spätere Outbox-Einträge, offene Detail-Sheets) durch die echte ID ersetzt.
* **Zusammenfassen** (bevor gesendet wird, nur für noch nicht gesendete Einträge):
  * create + update(s) → ein create mit dem Endstand
  * create + delete → beide fallen weg (der Server sieht nie etwas)
  * update + update (gleicher Termin, gleicher scope) → ein update
  * update + delete → delete
* **Reihenfolge:** strikt der Reihe nach (FIFO). Ein Eintrag im Zustand `conflict` blockiert **nur Einträge
  desselben Termins**, die anderen laufen weiter.
* **Nie stillschweigend verwerfen.** Fehler werden zu `error` bzw. `conflict` und tauchen in der Oberfläche auf.

### Lokale Vorschau (optimistisch)
* create: neues Vorkommen in `occ` (bei Serie: Vorschau nur für die nächsten Vorkommen im Zeitraum, einfache Regeln
  täglich/wöchentlich/monatlich/jährlich clientseitig; komplexe Regeln zeigen nur das erste Vorkommen + Hinweis
  „Serie wird nach dem Abgleich vollständig angezeigt“).
* update ohne Serie / scope=this: Vorkommen direkt ändern. scope=all/following: alle betroffenen Vorkommen um dieselbe
  Zeitdifferenz verschieben bzw. Felder übernehmen.
* delete: Vorkommen ausblenden (`_pending: "delete"`), damit „Rückgängig“ lokal sofort geht.
* Alle betroffenen Termine tragen den **Wolken-Badge** (gibt es schon) bis zur Bestätigung.

---

## 5. Abgleich (Sync)

### 5.1 Senden
* Auslöser: `online`-Event, App-Start, App wird sichtbar (`visibilitychange`), Live-Stream verbindet neu, alle 60 s
  solange etwas wartet, und **Background Sync** (`sync`-Event im Service Worker, Chrome/Android) – dann wird auch
  bei geschlossener App gesendet.
* **Sperre:** nur **ein** Sender gleichzeitig (Web Locks API `navigator.locks`, Fallback Flag in IndexedDB) –
  sonst senden Fenster und Service Worker doppelt.
* Jeder Request trägt `Idempotency-Key: <opId>` (siehe §6). Ein Wiederholen ist dadurch immer sicher.
* Antworten:
  * 2xx → Eintrag löschen, Server-Antwort in `occ`/`events` übernehmen, IDs umschreiben.
  * Netzfehler / 5xx / 429 → bleibt `pending`, erneut mit Backoff (5 s, 15 s, 1 min, 5 min …).
  * **401 / Weiterleitung zum Access-Login** → Zustand „Anmeldung abgelaufen“ (siehe §7), nichts verwerfen.
  * 409 (Termin inzwischen geändert) → `conflict` (§5.3).
  * 404 (Termin auf dem Server gelöscht) → `conflict` mit Wahl „Neu anlegen / Verwerfen“.
  * 400/403 → `error` mit Text vom Server, Wahl „Bearbeiten / Verwerfen“.

### 5.2 Nachladen (Delta statt alles)
* Neuer Endpunkt `GET /api/v1/sync?since=<cursor>&from=&to=` (§6): liefert alle seit `cursor` geänderten
  Termine als fertige Vorkommen im Zeitraum, gelöschte Termin-IDs und den neuen `cursor`.
* Erstmalig oder wenn der Cursor zu alt ist (Verlauf wird nach 90 Tagen aufgeräumt) → Antwort `full: true` →
  Zeitraum komplett neu laden.
* Reihenfolge immer: **erst senden, dann nachladen** – sonst überschreibt der alte Server-Stand die eigene Vorschau.
  Vorkommen mit offenem Outbox-Eintrag werden beim Nachladen **nicht** überschrieben.
* Der Live-Stream (SSE) stößt bei jeder Änderung ein kleines Nachladen an (heute: alles neu rendern).

### 5.3 Konflikte
* Erkennung über `sequence`: jeder update/delete schickt die `sequence`, auf der die Änderung basiert
  (`baseSequence`). Hat sich der Termin auf dem Server seitdem geändert (z. B. Ida hat ihn verschoben) → 409.
* Dialog (wie in §11 geplant): „**Meine Version** / **Server-Version**“ mit den unterschiedlichen Feldern
  nebeneinander (Zeit, Titel, Ort …). Optional „Zusammenführen“, wenn sich verschiedene Felder geändert haben
  (z. B. ich ändere den Ort, Ida die Zeit → beides übernehmen, automatisch, nur mit Hinweis).
* Gelöschte Termine: „Wurde inzwischen gelöscht – neu anlegen?“.

---

## 6. Backend-Änderungen (Kalender-Dienst)

| Änderung | Warum |
|---|---|
| **Idempotenz:** Header `Idempotency-Key` bei POST/PATCH/DELETE; neue Tabelle `requests(key, status, response, at)`, 14 Tage aufbewahren. Gleicher Key → gespeicherte Antwort zurück, nichts doppelt. | Verbindung bricht nach dem Speichern ab → kein Doppel-Termin. |
| **POST mit bekannter `uid`** → vorhandenen Termin zurückgeben (200, `existing: true`) statt Fehler. | Zweite Sicherung gegen Doppelte (auch für ICS-Import). |
| **DELETE mit `?sequence=`** → 409, wenn inzwischen geändert. | Offline gelöscht, aber jemand hat ihn zwischenzeitlich geändert → nachfragen. |
| `sequence` in jeder Vorkommen-Antwort (`occ_view`) | Die App kennt für jeden Termin den Basis-Stand. |
| **`GET /api/v1/sync?since&from&to`** → `{cursor, full, upserts:[…Vorkommen], deleted:[event_id], calendars?, settings?}` | Schnelles Delta-Nachladen statt ±3 Monate komplett. |
| Neue Alembic-Migration `0002_requests` | Tabelle für Idempotenz. |
| `GET /api/v1/meta` bekommt `api: 2` und `min_client` | Eine alte App merkt, dass sie aktualisieren muss, statt falsch zu senden. |
| API bleibt **abwärtskompatibel** (alte Felder/Endpunkte bleiben) | Ein Handy mit alter App-Version und offenen Änderungen darf nach dem Server-Update noch senden. |

---

## 7. PWA / Service Worker

* **Hülle offline:** wie heute versionierte Dateien; zusätzlich `"/"` und alle Routen (`/monat/…`, `/termin/…`)
  fallen offline auf die gecachte Hülle zurück (gibt es schon für Navigation).
* **Updates sicher machen:**
  1. Neuer SW installiert sich, **wartet** aber (kein sofortiges `skipWaiting`).
  2. Die Seite zeigt „Neue Version da · Neu laden“. Neu geladen wird **erst, wenn die Outbox leer ist** oder der Nutzer
     es ausdrücklich will (die Outbox liegt in IndexedDB und überlebt das Update ohnehin).
  3. Alte Caches werden erst gelöscht, wenn **kein** Fenster mehr die alte Version benutzt (`clients.matchAll`).
  4. Kein Fenster: beim nächsten Öffnen ist die neue Version direkt aktiv.
* **Background Sync:** Outbox-Senden im SW (`sync`-Tag `kal-outbox`, Chrome/Android). iPhone kann das nicht → dort
  wird beim nächsten Öffnen gesendet (wird in den Einstellungen so erklärt).
* **Cloudflare Access:** API-Aufrufe mit `redirect: "manual"`; eine Weiterleitung (opaqueredirect) oder 401/403 mit
  HTML heißt „Anmeldung abgelaufen“. Dann: Leiste „Bitte neu anmelden – 3 Änderungen warten“ + Knopf, der die Seite
  normal lädt (Cloudflare-Login), danach wird automatisch gesendet. **Nie als offline werten.**
  Zusätzlich prüfen: Access-Sitzungsdauer (720 h) reicht; Hinweis in den Einstellungen.
* `navigator.storage.persist()` (siehe §3).

---

## 8. Oberfläche

* Leiste unter der Top-Bar: „Offline – Stand 14:02 · 3 Änderungen warten“ (gibt es teilweise) bzw.
  „Wird abgeglichen …“ / „Anmeldung abgelaufen“.
* Wolken-Badge an jedem noch nicht bestätigten Termin (gibt es schon für Karten; auch für Raster-Blöcke und Pillen).
* Drawer/Einstellungen → **„Abgleich“**: Liste der wartenden Änderungen („Zahnarzt angelegt · offline um 14:02“),
  pro Eintrag „Erneut senden“ / „Verwerfen“, Konflikte zuoberst, „Jetzt abgleichen“, Stand und Speicherbedarf.
* Snackbar nach erfolgreichem Abgleich: „3 Offline-Änderungen gespeichert“.
* Konflikt-Sheet (§5.3).
* Offline-Hinweise, wo etwas wirklich nicht geht: Ida fragen, Abo-Link, Backups, Import → ausgegraut mit „braucht Internet“.
* Suche offline: über `occ` (gibt es schon, wird auf den neuen Store umgestellt).

---

## 9. Grenzen (bewusst)

* **Erinnerungen offline:** Die Erinnerungen löst der Server aus. Ist das Handy offline, kommt kein Push.
  Solange die App offen ist, prüft sie zusätzlich lokal (aus `occ`) und zeigt die Erinnerung selbst.
  Zuverlässig ohne Netz geht es nur über das Abo/CalDAV in der Handy-Kalender-App (DAVx⁵) – bleibt optional.
* **Komplexe Serienregeln** werden offline nur angenähert angezeigt (§4), gespeichert wird korrekt.
* **Anhänge offline** erst in Phase 4 (Blob in IndexedDB, Upload nach dem Termin).
* Ida-Tools arbeiten direkt am Server; Ida sieht Offline-Änderungen erst nach dem Abgleich.

---

## 10. Tests – damit es auch nach Updates sicher funktioniert

### 10.1 Backend (pytest, `kalender/tests/test_sync.py`)
* Idempotenz: gleicher Key zweimal → ein Termin, gleiche Antwort; anderer Key → zweiter Termin.
* POST mit vorhandener `uid` → `existing: true`, kein Doppel.
* PATCH/DELETE mit veralteter `sequence` → 409; mit aktueller → ok.
* `/sync`: Delta nach create/update/delete, Serien (Vorkommen im Zeitraum), `full` bei zu altem Cursor, Rechte für Agents.
* Migration `0002` läuft auf einer v1-Datenbank (Kopie mit Testdaten) ohne Datenverlust.

### 10.2 Logik der App ohne Browser (Node, `node --test`, keine Abhängigkeiten)
* `outbox.js` als reines Modul: Zusammenfassen (alle Kombinationen aus §4), ID-Umschreiben `local_` → echt,
  Reihenfolge, Blockieren bei Konflikt nur für denselben Termin, Backoff.
* Lokale Vorschau: create/update/delete/Serie auf `occ`.

### 10.3 Ende-zu-Ende im echten Browser (Playwright, `kalender/e2e/`, läuft in GitHub Actions)
Echter Kalender-Dienst + Chromium, Handy-Viewport, `context.set_offline(True/False)`:
1. Online laden → offline schalten → **Seite neu laden** → Kalender erscheint aus dem Cache mit allen Terminen.
2. Offline: Termin anlegen, bearbeiten, verschieben, anderen löschen → sofort sichtbar mit Wolke.
3. Online → automatisch gesendet, Wolken weg, Server-Daten stimmen (per API geprüft), **keine Doppelten**.
4. Verbindung bricht **während** des Sendens ab (Route wird nach dem Request gekappt) → erneut gesendet, kein Doppel.
5. Konflikt: offline ändern, parallel per API (als „Ida“) ändern, online → Konflikt-Sheet, beide Wahlmöglichkeiten.
6. Offline angelegt + offline gelöscht → Server sieht nichts.
7. Access abgelaufen simuliert (API antwortet mit 302 auf fremde Domain) → „Anmeldung abgelaufen“, nichts verworfen.
8. **Update-Test:** App-Version A laden, offline Änderungen machen, Dateien/Version ändern (Version B, neuer SW),
   neu laden → Outbox ist noch da, wird gesendet, neue Version aktiv, alter Cache weg.
9. **Schema-Test:** IndexedDB im alten Format (v1 + `kal-queue` in localStorage) vorbefüllen → neue App migriert,
   nichts geht verloren.

### 10.4 CI
* Neuer Job `kalender-e2e` in `.github/workflows/docker-publish.yml` (Playwright mit Chromium), blockiert das Image
  genauso wie die anderen Tests.
* Zusätzlich ein **Upgrade-Test gegen das zuletzt veröffentlichte Image** (`ghcr.io/…/ida-kalender:latest` starten,
  Daten anlegen, Container mit dem neuen Build auf demselben Volume starten → Migration ok, Daten da).

---

## 11. Umsetzung in Schritten

| Phase | Inhalt | Ergebnis |
|---|---|---|
| **1 – Fundament** | IndexedDB-Schema v2 mit Migration (inkl. Übernahme `kal-queue`), `storage.persist()`, Access-Ablauf erkennen, Fehler nie mehr verwerfen, SW-Update sicher (wartet, „Neu laden“) | Nichts geht mehr verloren, Updates sind sicher |
| **2 – Offline lesen** | `occ`/`events`-Stores, UI liest lokal, Zeitraum −3/+12 Monate, Backend `/sync` + `sequence` in Antworten, Delta-Nachladen, Detail offline | Kalender komplett offline lesbar, immer aktuell |
| **3 – Offline schreiben** | Outbox mit Zusammenfassen, lokale IDs, optimistische Vorschau, Idempotenz-Key + Tabelle, POST mit `uid`, DELETE mit `sequence`, Konflikt-Sheet, „Abgleich“-Ansicht, Background Sync | Anlegen/Ändern/Löschen offline, sauberer Abgleich |
| **4 – Rest** | Anhänge offline, lokale Erinnerungen bei offener App, Offline-Hinweise an allen Online-Funktionen | Rund |
| **Tests** | laufen ab Phase 1 mit: Backend-Tests, Node-Tests, Playwright inkl. Update- und Schema-Test, Upgrade-Test gegen `latest` in CI | Jedes Update wird automatisch auf Offline-Fähigkeit geprüft |

Geschätzter Umfang: Phase 1 klein, Phase 2 mittel, Phase 3 der größte Teil (Outbox + Konflikte + E2E-Tests), Phase 4 klein.
