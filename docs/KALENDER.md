# IDA Kalender – Plan

Ein **eigener, vollständiger Kalender, der komplett lokal läuft**:

* **Eigene Speicherung**, getrennt von IDA: eine eigene Datenbankdatei in einem eigenen Volume.
* **Keine Verbindung zu Google** oder einem anderen Cloud-Dienst, kein CDN, keine Telemetrie.
* **Eigener Dienst im Stack, auf eigenem Port**, extern über den Tunnel erreichbar und **als PWA installierbar**.
* **In IDA eingebunden:** Er erscheint als Seite `/kalender` in der IDA-Oberfläche.
* **Ida und alle Agents bedienen ihn direkt:** lesen, anlegen, ändern, verschieben, löschen, freie Zeit suchen.
* **Design:** dasselbe Material 3 Expressive (Android-17-Look) wie die IDA-Seite.

---

## 1. Grundsätze

| Grundsatz | Was das konkret heißt |
|---|---|
| **Alles lokal** | Daten liegen nur auf dem eigenen Server. Der Dienst baut **keine** ausgehenden Verbindungen auf. Schriften und Icons werden selbst ausgeliefert, nicht von `fonts.googleapis.com`. |
| **Getrennt gespeichert** | Eigene Datenbank `kalender.db` (SQLite) im Volume `kalender_data`. IDA greift **nie** direkt auf diese Datei zu, nur über die Kalender-API. Umgekehrt kennt der Kalender die IDA-Datenbank nicht. |
| **Eigenständig** | Der Kalender läuft auch, wenn IDA gerade nicht läuft oder neu startet. Er hat eigene Migrationen, einen eigenen Scheduler und eigene Backups. Man könnte ihn sogar ganz ohne IDA betreiben. |
| **Ida ist ein Client** | Ida nutzt dieselbe API wie die Oberfläche. Jede Änderung ist protokolliert, mit dem Agent als Urheber, und lässt sich rückgängig machen. |
| **Offene Formate** | Import und Export als `.ics` (iCalendar). Dazu ein Abo-Link fürs Handy und optional CalDAV. Die Daten kommen also jederzeit wieder heraus. |

**Ausdrücklich nicht dabei:**
* kein Google-Sync, kein Google-Connector
* keine fremden ICS-URLs, die der Server regelmäßig abholt
* keine Karten- oder Wetterdienste

Feiertage werden lokal berechnet (Python-Paket `holidays`, Bundesland einstellbar), es wird nichts heruntergeladen.

---

## 2. Architektur

```
                   ┌──────────────────────── Docker-Compose-Stack "ida" ────────────────────────┐
Browser/IDA-PWA ─:8080─> app (IDA, unverändert in seiner Datenbank)                             │
                   │      ├─ /kalender               IDA-Seite, lädt die Kalender-Oberfläche    │
                   │      ├─ /kal/*  ──Proxy──────────┐ (gleicher Origin, kein CORS)            │
                   │      ├─ hub.py calendar_*  ──HTTP┤ Ida-Tools = dünne API-Clients            │
                   │      └─ /api/v1/ext/kalender ◄───┼── Webhook: Erinnerung fällig → Infos    │
                   │                                  ▼                                          │
Kalender-PWA ─:8082─> kalender (eigenes Image, eigener Prozess)                                 │
 (extern über      │      ├─ /                       eigenständige Kalender-App + PWA           │
  Cloudflare)      │      ├─ /api/v1/*               REST-API (Oberfläche, IDA, Skripte)         │
                   │      ├─ /api/v1/stream          Live-Updates (SSE)                         │
                   │      ├─ /ics/<token>.ics        Abo-Link (nur lesen)                       │
                   │      ├─ /mcp                    MCP-Endpunkt (Token), optional              │
                   │      ├─ /dav/*                  CalDAV (Phase 6, optional)                 │
                   │      └─ Scheduler               Erinnerungen, Papierkorb, Backups           │
                   │            │                                                                │
                   │            ▼                                                                │
                   │      Volume kalender_data:/data   kalender.db (SQLite, WAL) + backups/      │
                   └─────────────────────────────────────────────────────────────────────────────┘
```

### Warum so?
* **Getrennte Speicherung:** Der Kalender ist ein eigener „Besitzer“ seiner Daten.
  * Ein Backup ist eine einzige Datei.
  * Ein IDA-Update oder eine IDA-Migration kann die Termine nicht beschädigen.
  * Ein Umzug heißt: Volume kopieren.
* **SQLite statt einer eigenen Postgres-Instanz:** ein Nutzer, wenige tausend Termine, ein schreibender Prozess. Das ist schnell genug.
  * Es gibt keinen zusätzlichen Container und kein zusätzliches Passwort.
  * WAL-Modus sorgt dafür, dass Lesen und Schreiben sich nicht blockieren.
  * Der Code nutzt SQLAlchemy async (wie IDA). Ein späterer Wechsel auf Postgres wäre nur eine andere `DATABASE_URL`.
* **Eigenes Image** `ghcr.io/l8tenever/ida-kalender`, aus dem Ordner `kalender/` im selben Repo.
  * Es ist klein: nur FastAPI, SQLAlchemy, dateutil und icalendar.
  * Am Port hängt **nur** der Kalender, nichts aus IDA (Chats, SSH, Terminal, Connectoren). Das ist wichtig, weil der Port nach außen geht.
  * Ein eigener Origin bedeutet eine eigene PWA mit eigenem Namen, Icon und Service Worker.
* **IDA-Einbindung über einen Proxy:** IDA leitet `/kal/*` intern an `http://kalender:8000` weiter.
  * Die IDA-Seite `/kalender` lädt dieselben JS-Module über `/kal/s/<version>/…` und spricht die API über `/kal/api/v1`.
  * Dadurch gibt es nur **eine** Kalender-Oberfläche, sie wird nicht doppelt gepflegt.
  * Weil alles unter demselben Origin läuft, gibt es kein CORS-Problem und kein zweites Cloudflare-Login.

### Live-Updates
* Jede Änderung schreibt eine Zeile in `changes` (die Tabelle braucht es fürs Rückgängig ohnehin) und weckt einen In-Memory-Bus.
  Das funktioniert hier, weil es nur **einen** Kalender-Prozess gibt.
* `GET /api/v1/stream` (SSE) sendet `{"type":"changed","ids":[…],"by":"ida"}`. Die Oberfläche lädt dann den sichtbaren Bereich neu.
  Legt Ida im Chat einen Termin an, erscheint er sofort in der offenen App.
* Beim Neuverbinden schickt der Client `Last-Event-ID`. Der Server liefert die verpassten Änderungen aus `changes` nach.

---

## 3. Datenmodell (SQLite, eigene Alembic-Kette in `kalender/`)

### `calendars`
| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | String(12) | Zufalls-ID |
| `name` | String | „Privat“, „Schule“, „Arbeit“, „Geburtstage“, „Ida“ … |
| `color` | String(16) | Schlüssel aus der Palette (§6), z. B. `tuerkis` |
| `kind` | String(10) | `normal` \| `birthdays` (jährlich, mit Alter) \| `holidays` (berechnet, nur lesen) |
| `visible` | Bool | Häkchen in der Seitenleiste |
| `default_alarms` | JSON | Standard-Erinnerungen für neue Termine, z. B. `[10]` |
| `agent_access` | String(8) | `write` \| `read` \| `none`: was Agents hier dürfen. Das stellt nur der Nutzer in der Oberfläche ein. |
| `position`, `is_default` | Int, Bool | Reihenfolge; Ziel, wenn nichts angegeben ist |
| `created_at`, `updated_at` | DateTime | |

### `events`
| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | String(12) | |
| `calendar_id` | FK → calendars (CASCADE) | |
| `uid` | String, unique | iCalendar-UID (für ICS und CalDAV stabil) |
| `title`, `description`, `location` | Text | Der Ort ist reiner Text, es werden keine Kartendaten abgefragt |
| `start_at`, `end_at` | DateTime (UTC) | Bei ganztägigen Terminen das lokale Datum |
| `all_day` | Bool | Das Ende ist **exklusiv** (wie bei ICS) |
| `tzid` | String | Zeitzone (Standard `Europe/Berlin`). Wichtig, damit Wiederholungen über die Zeitumstellung stimmen |
| `rrule` | String | z. B. `FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=…`; leer heißt einmalig |
| `exdates` | JSON | ausgelassene Vorkommen |
| `recurrence_parent_id`, `recurrence_original_start` | | einzeln geänderte Vorkommen („nur dieser Termin“) |
| `status` | String | `confirmed` \| `tentative` \| `cancelled` |
| `color` | String? | überschreibt die Kalenderfarbe |
| `alarms` | JSON | Minuten vorher, z. B. `[10, 1440]` |
| `alarm_action` | String | `notify` (Benachrichtigung) \| `agent` (IDA bekommt eine Aufgabe, z. B. „Erinnere mich, Unterlagen einzupacken“) |
| `created_by`, `updated_by` | String | `user` oder Agent-ID, für den Chip „von Ida“ |
| `sequence`, `etag` | Int, String | für ICS und CalDAV |
| `deleted_at` | DateTime? | Papierkorb (30 Tage, danach endgültig gelöscht) |
| `created_at`, `updated_at` | DateTime | |

Indizes: `(calendar_id, start_at)`, `end_at`, `rrule IS NOT NULL`, `deleted_at`.
Dazu eine Volltextsuche über **SQLite FTS5** (`events_fts`: Titel, Beschreibung, Ort), vollständig lokal.

### `alarms_sent`
Primärschlüssel `(event_id, occurrence_start, minutes)`. Jede Erinnerung feuert **genau einmal**, auch bei Serien, nach Neustarts und bei doppeltem Tick.

### `changes` (Verlauf, Live-Bus, Rückgängig)
`id` (autoincrement), `event_id`, `action` (`create|update|delete|restore`), `by` (`user` \| Agent-ID), `via` (`ui|ida|mcp|ics|dav`), `before` (JSON), `after` (JSON), `at`.
Einträge, die älter als 90 Tage sind, werden aufgeräumt.

### `settings`
Schlüssel-Wert-Paare:
* Wochenstart, Standardansicht, Arbeitszeiten, KW anzeigen, Bundesland für Feiertage
* Token für Abo-Link, IDA und MCP (als Hash gespeichert)
* Ziel-URL für den IDA-Webhook

### Wiederholungen
* Die Berechnung läuft auf dem Server mit **`python-dateutil`** (`rrulestr`), in der Zeitzone `tzid`, danach wird nach UTC umgerechnet.
  So bleibt „jeden Montag 08:00“ auch nach der Zeitumstellung um 08:00.
* `GET /events?from&to` liefert **aufgelöste Vorkommen**, jeweils mit `occurrence_id = "<id>_<YYYYMMDDTHHMM>"`.
* Ändern oder Löschen eines Vorkommens braucht einen `scope`:
  * `this`: Ausnahme-Zeile anlegen und `exdate` setzen
  * `following`: die alte Serie per `UNTIL` beenden und eine neue Serie anlegen
  * `all`: den Grundtermin ändern
* Geburtstage sind jährliche Serien. Die Anzeige rechnet das Alter aus, wenn ein Geburtsjahr hinterlegt ist („Anna wird 30“).

---

## 4. Kalender-Dienst (Backend)

### Ordner im Repo
```
kalender/
  Dockerfile                  python:3.12-slim, nicht-root, HEALTHCHECK /api/v1/health
  requirements.txt            fastapi, uvicorn, sqlalchemy[asyncio], aiosqlite, alembic,
                              python-dateutil, icalendar, holidays, pydantic-settings
  alembic.ini, alembic/versions/0001_init.py
  app/
    main.py                   App-Fabrik, Lifespan (Migrationen → Scheduler), statische Dateien /s/<version>/
    config.py                 DATA_DIR, TIMEZONE, PUBLIC_URL, IDA_WEBHOOK_URL, Tokens
    db.py                     Engine (SQLite: WAL, foreign_keys=ON, busy_timeout)
    models.py                 Calendar, Event, AlarmSent, Change, Setting
    schemas.py                EventCreate, EventUpdate (mit scope), Occurrence, CalendarIn …
    auth.py                   Rollen: Oberfläche (hinter Cloudflare Access) | Token „ida“ | Token „mcp“ | Feed-Token
    services/
      store.py                CRUD, Papierkorb, Verlauf schreiben, Rückgängig
      recurrence.py           RRULE auflösen, Ausnahmen, scope this/following/all
      query.py                Bereich abfragen, Suche (FTS5), freie Zeit, Konflikte, Tagesübersicht
      alarms.py               tick(): fällige Erinnerungen → Live-Bus + IDA-Webhook (+ optional Push)
      holidays.py             Feiertage berechnen (virtueller Kalender, nur lesen)
      ics.py                  Import/Export, Abo-Feed
      parse.py                Schnelleingabe „morgen 15 Uhr Zahnarzt 1h“ → Termin (Regeln, ohne KI)
      backup.py               nächtlich VACUUM INTO + ICS-Export, 14 Stände behalten
      bus.py                  In-Memory-Bus für SSE
    api/
      calendars.py, events.py, changes.py, query.py, ics.py, settings.py, stream.py
      mcp.py                  Streamable-HTTP-MCP (dieselben Tools wie in IDA)
  tests/                      pytest, eigene Suite
frontend/kalender/            (siehe §10, wird ins Kalender-Image kopiert)
```

### REST-API (`/api/v1`)
| Methode + Pfad | Zweck |
|---|---|
| `GET /calendars` · `POST` · `PATCH /{id}` · `DELETE /{id}` | Kalender verwalten |
| `GET /events?from=&to=&calendars=&q=` | Vorkommen im Bereich (aufgelöst) |
| `GET /events/{occurrence_id}` | ein Termin oder Vorkommen mit Serie |
| `POST /events` | anlegen |
| `PATCH /events/{occurrence_id}?scope=this\|following\|all` | ändern oder verschieben |
| `DELETE /events/{occurrence_id}?scope=…` | in den Papierkorb |
| `GET /trash` · `POST /events/{id}/restore` · `DELETE /trash` | Papierkorb |
| `GET /changes?by=&since=` · `POST /changes/{id}/undo` | Verlauf, Rückgängig (auch für Agent-Änderungen) |
| `GET /freebusy?from=&to=&duration=&within=` | freie Zeitfenster |
| `GET /conflicts?start=&end=` | Überschneidungen |
| `GET /brief?date=` | kompakte Tagesübersicht |
| `GET /search?q=` | Volltextsuche |
| `POST /quick` | Schnelleingabe → Vorschlag (noch nicht gespeichert) |
| `POST /import` (multipart .ics) · `GET /export.ics?calendar=` | ICS |
| `GET /feed` · `POST /feed/rotate` | Abo-Link verwalten |
| `GET /stream` | SSE-Live-Updates |
| `GET /settings` · `PUT` | Einstellungen |
| `GET /backups` · `POST /backups` · `GET /backups/{name}` | Backups ansehen, sofort anlegen, herunterladen |
| `GET /health` | Healthcheck |

### Zugriff und Rechte
Es gibt vier Wege in den Dienst:

* **Oberfläche:** Sie hat keine eigene Anmeldung, wie IDA. Davor steht Cloudflare Access. Im LAN ist der Port direkt offen.
* **Token `ida`:** für die Hub-Tools und den Proxy in IDA. Wird nur im internen Docker-Netz benutzt.
  * Der Header `X-Kalender-By: <agent-id>` setzt den Urheber.
  * `agent_access` wird **im Kalender-Dienst** durchgesetzt. So kann IDA es auch nicht aus Versehen umgehen.
* **Token `mcp`:** für das optionale externe MCP. Es gelten dieselben Agent-Regeln.
* **Feed-Token:** nur lesend, nur für `/ics/<token>.ics`.

Tokens stehen in `.env`, in der Datenbank liegen nur ihre Hashes.

### Scheduler (im Kalender-Dienst)
* `alarms.tick()` alle 15 s; es prüft nur die nächsten 48 h. Was fällig ist, geht auf drei Wegen raus:
  1. per SSE an alle offenen Kalender-Fenster. Dort erscheint eine Snackbar, auf Wunsch zusätzlich eine `Notification`, solange die App offen ist.
  2. per Webhook an IDA (`POST $IDA_WEBHOOK_URL`). IDA macht daraus eine Nachricht unter Infos/Nachrichten, wie heute die Erinnerungen.
     Bei `alarm_action=agent` startet IDA stattdessen einen Agent-Lauf, auf demselben Weg wie `Reminder(kind="task")`.
  3. optional per Web Push (Phase 6, siehe §11).
* Ist IDA nicht erreichbar, versucht der Webhook es mit Backoff erneut, höchstens 1 h lang. Die Erinnerung geht also nicht verloren.
* Täglich um 03:30: Papierkorb aufräumen, `changes` kürzen, Backup.

---

## 5. Ida als Agent – Tools im internen MCP (`backend/app/services/mcp/hub.py`)

Neue Einträge in `_spec` mit denselben Rechte-Stufen wie bei Erinnerungen.
Die Handler sind **dünne HTTP-Clients** (`services/kalender_client.py`, httpx). Sie rufen `KALENDER_URL` mit dem Token `ida` auf und setzen `X-Kalender-By` auf die Agent-ID.

| Tool | Recht | Beschreibung (gekürzt) |
|---|---|---|
| `calendar_list` | ALL | Kalender mit Farbe und Agent-Recht |
| `calendar_events` | ALL | Termine in einem Zeitraum (`from`, `to` als JJJJ-MM-TT oder „heute“, „diese Woche“), optional `calendar`, `query` |
| `calendar_event_get` | ALL | ein Termin mit Serie und Ausnahmen |
| `calendar_event_create` | ALL | `title`, `start` sowie `end` **oder** `duration_min`, dazu `all_day`, `calendar`, `location`, `description`, Wiederholung (`rrule` oder einfach `repeat: daily/weekly/monthly/yearly`, `days`, `until`, `count`), `alarms`, `alarm_action` |
| `calendar_event_update` | ALL | wie oben, plus `id` und `scope` (`this`/`following`/`all`) |
| `calendar_event_move` | ALL | Kurzform: auf eine neue Zeit verschieben, die Dauer bleibt |
| `calendar_event_delete` | ALL | in den Papierkorb, rückgängig machbar, mit `scope` |
| `calendar_free_time` | ALL | freie Fenster: `from`, `to`, `duration_min`, `within` (z. B. 08:00–20:00), `days` |
| `calendar_conflicts` | ALL | prüft vor dem Anlegen, ob der Zeitraum frei ist |
| `calendar_day_brief` | ALL | kompakte Tagesübersicht, für Morgen-Routinen |
| `calendar_search` | ALL | Volltextsuche |
| `calendar_undo` | ALL | macht die letzte eigene Änderung rückgängig, oder eine bestimmte per `change_id` |
| `calendar_create` / `calendar_update` | MAIN_LIVE | Kalender anlegen, umbenennen, Farbe setzen (nur der Haupt-Agent im Chat) |
| `calendar_import_ics` | MAIN | eine `.ics`-Datei aus dem Chat importieren, z. B. eine Einladung |

### Regeln
* **Zeiten sind Ortszeit** (`TIMEZONE`). Die Beschreibung lautet wie bei `reminder_create`: „Rechne ‚morgen‘ mit der aktuellen Uhrzeit um.“
* **`agent_access`** gilt hart und wird im Kalender-Dienst geprüft:
  * `read`: Der Agent darf nur lesen.
  * `none`: Der Kalender ist für Agents unsichtbar.
* **Löschen ohne Rückfrage**: Alles landet im Papierkorb, und `calendar_undo` holt es zurück.
* **Sichtbarkeit von Agent-Änderungen:** Jede steht in `changes`.
  * Im Kalender trägt der Termin den Chip „von Ida“ (mit Agent-Farbe und -Bild).
  * Im Detail-Sheet gibt es einen Knopf „Rückgängig“.
* **Prompt** (`claude/prompts.py`):
  * Der Hinweis lautet: „Für Termine benutze calendar_*; reminder_create nur für Erinnerungen ohne festen Termin.“
  * Dazu kommt ein kurzer Kontextblock mit den Terminen der nächsten 24 h. Er ist abschaltbar und wird per API geholt, mit kurzem Cache.
* **Termin-Karte im Chat:** Titel, Zeit und Farbe, dazu die Knöpfe „Öffnen“ (springt zu `/kalender/termin/<id>`) und „Rückgängig“.
* **Kalender nicht erreichbar:** Das Tool meldet einen klaren Fehler („Kalender-Dienst nicht erreichbar“), IDA selbst läuft normal weiter.

### Startseite
`services/home_calendar.py` bekommt die Quelle `{"source": "kalender"}` als **Standard**.
* Die heutigen Termine kommen per API vom Kalender-Dienst.
* Ein Klick auf einen Termin öffnet ihn in `/kalender`.
* Der bisherige MCP-Connector-Weg wird entfernt.

### Proxy in IDA
`backend/app/api/kalender_proxy.py` leitet `/kal/{path}` an `http://kalender:8000/{path}` weiter.
* Er streamt, damit SSE funktioniert.
* Er setzt das `ida`-Token mit `X-Kalender-By: user`.
* Die IDA-Seite braucht dadurch weder CORS noch ein eigenes Login.

---

## 6. Design – Material 3 Expressive (Android 17)

### Gemeinsame Basis mit IDA
* `frontend/css/ida.css` wird aufgeteilt in **`css/tokens.css`** und **`css/components.css`**:
  * `tokens.css`: Farben, Flächen, Kurven `--spring`/`--emph`, Radien, Schrift
  * `components.css`: Icon-Buttons, Chips, Sheets, Menüs, Toast, Formulare, Segmented Buttons
* Das Kalender-Image kopiert beide Dateien beim Bauen (`COPY frontend/css/tokens.css …`). So bleibt der Stil garantiert gleich, und es gibt keine Kopie, die auseinanderläuft.
* **Schriften lokal:** Google Sans Flex und Material Symbols Rounded kommen als `woff2` nach `frontend/fonts/` und werden per `@font-face` geladen.
  * Das Icon-Font ist auf die benutzten Icons reduziert, damit es klein bleibt.
  * **IDA bekommt das gleich mit:** Der `fonts.googleapis.com`-Link in `index.html` fällt weg. Damit lädt auch IDA nichts mehr von Google.
* Schwarzer Hintergrund, Flächen `--surface` / `--surface-high` / `--surface-highest`.
* Akzent: Ida-Blau `--accent: #7fd3ff`. In IDA folgt er wie überall dem gewählten Agenten.

### Terminfarben (eigene Palette, für den dunklen Hintergrund abgestimmt)
Rot · Koralle · Orange · Gelb · Salbei · Grün · Türkis · Blau · Lavendel · Lila · Graphit.
* Jede Farbe hat drei Varianten:
  * `fill`: für den Termin-Block
  * `container`: gedämpft, für vergangene oder abgesagte Termine
  * `on`: Textfarbe mit Kontrast ≥ 4.5:1
* Die Palette liegt als JSON im Backend. Ein Test prüft den Kontrast.

### Bausteine und Verhalten
| Element | Umsetzung |
|---|---|
| **Kopfzeile** | großer Monatsname (Display-Small); links Menü, rechts Heute-Knopf (Icon mit Tageszahl) und Suche; Ansichtswahl als **Segmented Button** (Tag · 3 T · Woche · Monat · Liste) |
| **Seitenleiste** | am PC fest, schwebend mit runden Ecken wie die Code-Seitenleisten in IDA; Inhalt: Mini-Monat, Kalender mit farbigen Checkboxen, „Neuer Kalender“, Papierkorb, Verlauf („Was hat Ida geändert?“), Einstellungen |
| **FAB** | Expressive-FAB (großes, abgerundetes Quadrat, 96 px am Handy) mit `add`. Lange drücken öffnet ein FAB-Menü (Termin · Schnelleingabe · Ida fragen) mit Feder-Animation |
| **Monat** | 6×7-Raster, heute als gefüllter Kreis im Akzent; Termine als farbige Pillen (höchstens 3, dann „+2“), mehrtägige als durchgehende Balken; Feiertage dezent beschriftet |
| **Woche / 3 Tage / Tag** | Zeitraster mit 15-Minuten-Einrastung, **Jetzt-Linie** mit Punkt, Ganztags-Leiste oben; überlappende Termine nebeneinander (Spalten-Algorithmus); Arbeitszeiten heller hinterlegt |
| **Liste (Agenda)** | Tage als Gruppen mit klebender Überschrift, endloses Scrollen in beide Richtungen |
| **Jahr** | 12 Mini-Monate, Tage mit Terminen als Punkte |
| **Gesten** | Wischen = vor/zurück (mit Federn); Pinch = Stundenhöhe; lange drücken und ziehen = Termin aufziehen; Termin ziehen = verschieben; unterer Griff = verlängern; Haptik beim Einrasten |
| **Termin-Detail** | Bottom-Sheet, am PC Seitenpanel: Farbbalken, Titel, Zeit, Wiederholung im Klartext („Jeden Mo und Mi bis 20. Dez.“), Ort, Erinnerungen, Chip „von Ida“; Knöpfe Bearbeiten · Löschen · Duplizieren · Ida fragen |
| **Editor** | Vollbild-Sheet am Handy, Dialog am PC: großer Titel, Ganztägig-Schalter, Datum und Uhrzeit (`ui/timepicker.js`, dazu eine Datumswahl); Wiederholung als Chips plus „Benutzerdefiniert“; Kalenderwahl mit Farbe; Erinnerungs-Chips |
| **Serien-Rückfrage** | Dialog: „Nur dieser Termin / Dieser und alle folgenden / Alle Termine“ |
| **Snackbar** | „Termin gelöscht · Rückgängig“ |
| **Animationen** | Shared-Axis-Übergang beim Ansichtswechsel; federnde Sheets (`--spring`); Termin-Blöcke wachsen beim Anlegen auf; `prefers-reduced-motion` wird beachtet |
| **Leerer Zustand** | Maskottchen mit „Nichts geplant“ |

Responsive Stufen:
* **Unter 600 px (Handy):** Floating Toolbar, FAB und Bottom-Sheets, Standardansicht „Monat + Tag“ (Details in §7)
* **Ab 600 px (Tablet):** Woche
* **Ab 1000 px (PC):** feste Seitenleiste, Detail als Panel rechts, Tastenkürzel:
  * `T`: heute
  * `D`/`W`/`M`/`A`: Tag, Woche, Monat, Agenda
  * `C`: neuer Termin
  * `←`/`→`: blättern
  * `/`: Suche

---

## 7. Handy-Oberfläche im Detail

Das Handy hat Vorrang. Alles wird zuerst für **360–430 px Breite** gebaut, der PC ist die Erweiterung.
Vorbild ist Android 17 mit Material 3 Expressive:
* große, weiche Formen
* Bedienung mit dem Daumen unten
* Federn-Animationen
* kräftige Farben auf Schwarz

Ein visueller Entwurf der wichtigsten Screens liegt als `docs/kalender-mockup.html` bei.

### 7.1 Grundgerüst (alle Ansichten)

```
┌────────────────────────────────┐  ← Statusleiste (safe-area-inset-top)
│ ☰  Oktober 2026 ▾      🔍  [29]│  Top-Bar 64 px; Titel antippen → Mini-Monat klappt auf
├────────────────────────────────┤
│                                │
│         Inhalt der Ansicht     │  scrollt; Wischen links/rechts = blättern
│                                │
│                                │
│   ╭──────────────────────╮ ╭──╮│
│   │ ☰Liste  3T  Monat  … │ │＋││  Floating Toolbar + FAB (unten, Daumenbereich)
│   ╰──────────────────────╯ ╰──╯│
└────────────────────────────────┘  ← Gestenleiste (safe-area-inset-bottom)
```

| Teil | Maße und Verhalten |
|---|---|
| **Top-Bar** | 64 px hoch, transparent auf Schwarz. Links das Menü (48 px Icon-Button wie in IDA), daneben der Titel „Oktober 2026“ (22 px, 500) mit kleinem ▾. Rechts Suche und Heute-Knopf (Kalender-Icon mit Tageszahl; er pulsiert kurz, wenn man weit von heute weg ist). Beim Scrollen bekommt die Leiste eine `--surface`-Fläche. |
| **Mini-Monat** | Titel antippen: Ein Monatsraster federt von oben herunter (`--spring`). Tag antippen springt dorthin, Wischen wechselt den Monat, Tage mit Terminen haben einen Punkt. Schließen durch erneutes Antippen oder Hochwischen. |
| **Untere Leiste** (M3 Expressive Floating Toolbar + FAB) | Toolbar und FAB stehen **in einer Reihe** (Flex-Container, links und rechts je 12 px Rand, 12 px Abstand dazwischen), 14 px über der Gestenleiste (+ `safe-area-inset-bottom`). Sie können sich nie überlappen. **Toolbar:** 56 px hoch, Radius 28 px, Fläche `--surface-highest`, 4 px Innenabstand. Darin: drei Lieblingsansichten und „…“ für alle Ansichten. Inaktive Einträge sind 40 × 48 px und zeigen nur das Icon; der aktive ist eine Pille im Akzent-Container mit Icon und Namen (nicht umbrechend), die beim Wechsel mit einer Feder-Animation zur neuen Position wandert. **Hinter der Leiste** liegt ein Verlauf von Schwarz nach transparent (110 px), damit Termine darunter weich ausblenden und nicht mit der Leiste kollidieren. Beim Runterscrollen gleitet die Leiste nach unten weg, beim Hochscrollen kommt sie zurück. Am Listenende gibt es 96 px Leerraum, damit der letzte Termin über der Leiste lesbar ist. |
| **FAB** | rechts in der Leiste, **genauso hoch wie die Toolbar** (56 × 56 px), Radius 18 px, Akzentfarbe, Icon `add`. Antippen legt einen Termin zur nächsten vollen Stunde an. Lange drücken öffnet ein FAB-Menü (Termin · Schnelleingabe · Ganztägig · Aus Vorlage · Ida fragen), das nach oben aufspringt. Beim Drücken wird die Form runder, wie die Icon-Buttons in IDA. **Snackbars** erscheinen 12 px über der Leiste, damit sie die Leiste nie verdecken. |
| **Drawer** | Wischen vom linken Rand oder ☰. Breite 85 %, höchstens 360 px, rechts abgerundet (28 px). Inhalt siehe 7.10. |
| **Touch-Ziele** | mindestens 48 × 48 px, auch wenn das Sichtbare kleiner ist (z. B. die Pillen in der Monatsansicht) |
| **Schrift** | Google Sans Flex: 14 px für Inhalt, 12 px für Raster-Beschriftung; Datumszahlen in Ansichten 500er-Gewicht. Die Systemschriftgröße wird respektiert (rem). |
| **Haptik** | kurz (8 ms) beim Einrasten im Raster, beim Umschalten der Ansicht und beim Ablegen eines gezogenen Termins |
| **Zurück-Geste** | Android-Zurück schließt erst das Sheet, dann den Mini-Monat und den Drawer, dann geht es eine Ansicht zurück (History-API, wie in IDA) |

### 7.2 Ansicht „Monat + Tag“ (Standard am Handy)

```
│ Mo Di Mi Do Fr Sa So     KW │
│ 26 27 28 29 30 31  1     44 │  Monatsraster oben, ca. 55 % der Höhe
│ ·  ·· ·  ●  ·              │  Punkte in Kalenderfarben (höchstens 3)
│ ...                         │
├─────────────────────────────┤  Griff: ziehen = Raster größer/kleiner
│ Donnerstag, 29. Oktober     │
│ ▌08:00–09:30  Mathe     R12 │  Tagesliste des gewählten Tags
│ ▌15:00        Zahnarzt  ✦Ida│
```
* Oben das Monatsraster mit Punkten statt Text. Das ist auf 360 px lesbar, Text-Pillen wären es nicht.
* Unten die Termine des gewählten Tags als Liste.
* Den Griff dazwischen kann man nach oben ziehen: Dann wird das Raster zur Wochenzeile und die Liste groß. Nach unten gezogen zeigt das Raster Pillen mit Text (Vollmonat).
* Doppelt auf einen Tag tippen öffnet die Tagesansicht. Lange drücken legt dort einen neuen Termin an.
* Heute ist ein gefüllter Kreis im Akzent, der gewählte Tag ein Ring. Wochenenden und Feiertage haben eine gedämpfte Zahl, Feiertage dazu einen kleinen Text unter der Zahl.

### 7.3 Liste (Agenda)
* Sie scrollt endlos in beide Richtungen und startet bei heute.
* Tagesüberschriften kleben oben beim Scrollen („Heute“, „Morgen“, dann „Sa., 31. Okt.“).
* Ein Termin ist eine Karte, 72 px hoch, Radius 16 px, mit einem Farbbalken links (4 px):
  * Inhalt: Titel, darunter Zeit und Ort.
  * Rechts kleine Icons: 🔁 Serie, 🔔 Erinnerung, Chip „Ida“.
* Leere Tage werden zusammengefasst: „Nichts geplant · 2.–4. Nov.“.
* Monatswechsel bekommen ein großes Banner mit Monatsname und der Kalenderfarbe des Monats.
* **Wischen auf einer Karte:**
  * nach rechts: Erinnerung schlummern bzw. „Erledigt“-Haken bei vergangenen Terminen
  * nach links: Löschen, mit Snackbar „Rückgängig“
* Das Swipe-Ziel wird beim Ziehen farbig sichtbar, mit Icon. Die Karte federt zurück, wenn man nicht weit genug zieht.

### 7.4 Tag und 3 Tage
* Ein Zeitraster mit 64 px pro Stunde; per Pinch einstellbar zwischen 40 und 120 px, die Einstellung wird gemerkt.
* Beim Öffnen scrollt es zur aktuellen Stunde bzw. zum Beginn der Arbeitszeit.
* **Jetzt-Linie** im Akzent, 2 px, mit Punkt am linken Rand; sie läuft live mit.
* Ganztags-Leiste oben, einklappbar, wenn es mehr als 2 Einträge sind.
* **Termin-Blöcke:**
  * Radius 12 px, Füllung `fill`, Text `on`.
  * Ab 30 Min. Höhe stehen Titel und Zeit drin, darunter nur der Titel.
  * Überlappende Termine teilen sich die Breite nebeneinander (Spalten-Algorithmus), höchstens 3; danach „+2“.
* **Neuen Termin aufziehen:**
  * Lange auf eine freie Stelle drücken: Ein Geister-Block erscheint mit Haptik.
  * Dann nach unten ziehen, um die Dauer festzulegen (rastet auf 15 Min. ein).
  * Loslassen öffnet den Editor als halbes Sheet.
* **Termin verschieben:**
  * Lange auf den Block drücken: Er hebt sich ab (Schatten, Skalierung 1.03).
  * Ziehen verschiebt ihn, auch über den Bildschirmrand in den nächsten Tag (der Rand scrollt automatisch).
  * Am unteren Griff zieht man ihn länger.
  * Danach kommt eine Snackbar mit „Rückgängig“; bei Serien vorher der Serien-Dialog.
* **Reisezeit** erscheint als schraffierter Bereich vor dem Termin.

### 7.5 Woche (am Handy quer oder auf Wunsch)
* 7 schmale Spalten. Die Blöcke zeigen nur den Titel, gekürzt.
* Antippen öffnet das Detail-Sheet, Pinch funktioniert wie in der Tagesansicht.
* Hochkant am Handy ist standardmäßig 3 Tage aktiv. Die Woche wird empfohlen, wenn das Handy quer gehalten wird.

### 7.6 Jahr
* 12 Mini-Monate in 2 Spalten (Handy) bzw. 4 Spalten (PC).
* Tage mit Terminen bekommen einen Punkt in der Farbe des dominanten Kalenders; Ferien- und Urlaubszeiträume erscheinen als farbiges Band.
* Einen Monat antippen öffnet die Ansicht „Monat + Tag“.

### 7.7 Termin-Detail (Bottom-Sheet)
* **Halbhoch:** Das Sheet öffnet halbhoch, Hochziehen macht es vollhoch, Runterziehen schließt es. Oben sitzt ein Griff, der Radius beträgt 28 px.
* **Aufbau von oben nach unten:**
  1. Farbstreifen über die ganze Breite
  2. Titel (24 px)
  3. Datum und Zeit, darunter die Dauer
  4. „Noch 3 Tage“ als Countdown-Chip
  5. Wiederholung im Klartext
  6. Ort mit Kopieren-Knopf
  7. Erinnerungen
  8. Kalender (Farbpunkt und Name)
  9. Beschreibung (Markdown, Checklisten abhakbar)
  10. Anhänge
  11. Verlauf, z. B. „von Ida angelegt · vor 2 Std. · Rückgängig“
* **Aktionsleiste unten** (klebt am Sheet-Ende, 16 px Rand, 44 px hoch): „Bearbeiten“ als gefüllter Button, der die freie Breite füllt, rechts daneben runde 44-px-Icon-Buttons für Duplizieren, Teilen (als `.ics` oder Text über die Web-Share-API) und Löschen. „Ida fragen“ und Weiteres liegen unter „…“ im Sheet-Kopf. So passt alles auch auf 360 px, ohne abgeschnitten zu werden.
* **Am PC** erscheint das Detail stattdessen als Panel rechts (400 px).

### 7.8 Editor (Vollbild-Sheet)
* **Kopf:**
  * links ✕ (verwirft, fragt bei Änderungen nach)
  * rechts „Speichern“ als gefüllter Button
* **Titel:** großes Feld (28 px), Autofokus. Während des Tippens erscheinen **Vorschläge** aus früheren Terminen („Zahnarzt“ → übernimmt Dauer, Kalender, Ort, Erinnerung).
* **Felder als Zeilen** mit Icon links, jeweils 56 px hoch:
  * Ganztägig (Schalter)
  * Start und Ende: Datum öffnet die eigene Datumswahl, Uhrzeit den `timepicker.js`. Die Dauer bleibt beim Verschieben des Starts erhalten.
  * Wiederholung: Chips „Nie · Täglich · Wöchentlich am Do · Monatlich am 29. · Jährlich · Werktags · Benutzerdefiniert“. Benutzerdefiniert öffnet ein Sheet mit Intervall, Wochentagen, „am 2. Dienstag“, Ende (nie / am / nach n Mal).
  * Kalender: Chip mit Farbe, öffnet eine Auswahl.
  * Farbe: Palette mit 11 Kreisen, Standard ist die Kalenderfarbe.
  * Erinnerungen: Chips „10 Min. · 1 Std. · 1 Tag · eigene“, mehrere möglich. Dazu ein Schalter „Ida kümmert sich“ für `alarm_action=agent` mit Textfeld für die Aufgabe.
  * Ort mit Autovervollständigung aus früheren Orten (lokal)
  * Reisezeit: 0 / 15 / 30 / 60 Min.
  * Status: Beschäftigt / Frei / Vorläufig
  * Beschreibung (Markdown, `- [ ]` für Checklisten)
  * Link und Anhänge
* Seltene Felder sind unter „Mehr Optionen“ eingeklappt, damit der Editor kurz bleibt.
* **Konflikt-Hinweis live:** Überschneidet sich der Termin mit einem anderen, erscheint eine gelbe Zeile: „Überschneidet sich mit ‚Mathe‘ (08:00–09:30)“.

### 7.9 Schnelleingabe (Sheet)
* **Einzeiliges Feld:** z. B. „morgen 15 Uhr Zahnarzt 1h @Praxis Müller #Privat !30m“.
* **Live-Vorschau:** Darunter zeigt eine Vorschau-Karte das erkannte Ergebnis. Die einzelnen Teile sind farbig markiert: Datum blau, Zeit grün, `#Kalender`, `@Ort`, `!Erinnerung`.
* **Enter speichert**, „Bearbeiten“ öffnet den vollen Editor mit den erkannten Werten.
* Die Erkennung läuft lokal über Regeln (`parse.py`), ohne KI.

### 7.10 Drawer
* Kopf: kleines Ida-Maskottchen und „Kalender“.
* Ansichten als Liste (für alle, die die Toolbar nicht mögen).
* **Kalender:** Farbpunkte mit Häkchen.
  * Lange drücken öffnet die Kalender-Einstellungen: Name, Farbe, Agent-Recht, Standard-Erinnerung, Export.
  * „Nur diesen anzeigen“ ist ein Doppeltipp.
* „Neuer Kalender“.
* Weitere Ziele:
  * Feiertage (an/aus, Bundesland)
  * Geburtstage
  * Papierkorb (mit Anzahl)
  * Verlauf („Was hat Ida geändert?“)
  * Import/Export
  * Einstellungen
  * App installieren

### 7.11 Einstellungen
Gruppierte Listen im Android-17-Stil: abgerundete Gruppen, die erste und letzte Zeile mit größerem Radius.
* **Allgemein:** Wochenstart, Standardansicht am Handy und am PC, Lieblingsansichten in der Toolbar, KW anzeigen, Arbeitszeiten, zweite Zeitzone, Design (System / Dunkel / Hell).
* **Termine:** Standarddauer, Standard-Erinnerung, Standardkalender, Reisezeit-Vorgabe.
* **Benachrichtigungen:** in der App, IDA-Infos, Push (Opt-in), Ruhezeiten.
* **Ida:** Agent-Rechte je Kalender, 24-h-Kontext an/aus, „Ida-Änderungen hervorheben“.
* **Daten:** Backups (Liste, jetzt sichern, herunterladen), Import, Export, Abo-Link (anzeigen, kopieren, QR-Code, rotieren), Papierkorb-Dauer.
* **Über:** Version, Speicherplatz, Anzahl Termine.

### 7.12 Zustände und Rückmeldungen
| Zustand | Darstellung |
|---|---|
| **Laden** | Skelett-Blöcke in Kalenderfarben mit sanftem Schimmern, nie ein leerer Bildschirm (die Daten aus IndexedDB erscheinen sofort, danach wird aktualisiert) |
| **Offline** | Unter der Top-Bar eine dünne Leiste „Offline – Stand 14:02“. Offline geänderte Termine haben einen kleinen Wolken-Badge, bis sie synchronisiert sind. |
| **Live-Änderung durch Ida** | Der neue oder geänderte Block blinkt einmal sanft im Akzent auf. Dazu eine Snackbar „Ida hat ‚Zahnarzt‘ angelegt · Ansehen · Rückgängig“. |
| **Erinnerung fällig, App offen** | Ein großes Sheet von unten mit Titel, Zeit, Ort und den Knöpfen „Schlummern“ (5 / 10 / 30 Min.) und „OK“; bei aktivierten Benachrichtigungen zusätzlich eine System-Notification |
| **Leer** | Das Maskottchen schaut, darunter „Nichts geplant“ und der Knopf „Termin anlegen“ |
| **Fehler** | Snackbar mit „Erneut versuchen“. Speichern geht nie verloren, denn es landet in der Offline-Warteschlange. |

### 7.13 Animationen (alle mit `prefers-reduced-motion`-Fallback)
* **Ansichtswechsel:** Shared Axis. Zwischen Ansichten wird horizontal überblendet; zwischen Tag und Monat wird gezoomt, der angetippte Tag wächst zur Tagesansicht.
* **Blättern:** Der Inhalt folgt dem Finger (1:1) und federt beim Loslassen in die nächste Periode.
* **Sheets und FAB-Menü:** `--spring`. Der FAB morpht beim Öffnen des Editors in das Sheet (Container Transform).
* **Neuer Termin:** Der Block wächst aus dem Antippunkt auf.
* **Toolbar:** Beim Wechsel wandert die aktive Pille zur neuen Position.

### 7.14 PC und Tablet (Kurzfassung)
* **Ab 600 px:** Die Toolbar wird zur festen Navigationsleiste links (Navigation Rail), der FAB sitzt oben in der Rail.
* **Ab 1000 px:**
  * Seitenleiste fest mit Mini-Monat und Kalendern; das Detail erscheint als Panel rechts.
  * Woche als Standard; Hover zeigt eine Termin-Vorschau.
  * Tastenkürzel (§6); Ziehen mit der Maus wie Touch.
  * Rechtsklick-Menü: Bearbeiten, Duplizieren, Farbe, Löschen.

---

## 8. Suche

Die Suche ist ein eigener Vollbild-Screen. Sie öffnet sich über 🔍 in der Top-Bar, am PC mit `/`.

### 8.1 Aufbau
```
┌────────────────────────────────┐
│ ←  [ zahnarzt          ✕ ]     │  Suchfeld mit Autofokus, Pille, 56 px
│ [Alle Kalender▾][Zeitraum▾]    │  Filter-Chips, waagerecht scrollbar
│ [🔁 Serien][🔔][✦ von Ida][…]  │
├────────────────────────────────┤
│ DEMNÄCHST                      │
│ ▌Do 05. Nov · 15:00  Zahnarzt  │  Treffer mit hervorgehobenem Suchwort
│ VERGANGEN                      │
│ ▌Mi 12. Jun · 10:00  Zahnarzt  │
└────────────────────────────────┘
```

### 8.2 Funktionen
| Funktion | Details |
|---|---|
| **Suche beim Tippen** | 150 ms nach dem letzten Tastendruck, ohne Enter |
| **Wo gesucht wird** | Titel, Ort, Beschreibung (inkl. Checklisten), Kalendername, Name von Geburtstags-Personen |
| **Tolerant** | Groß- und Kleinschreibung egal; ä = ae, ß = ss; Präfixe („zahn“ findet „Zahnarzt“); mehrere Wörter werden mit UND verknüpft; „Wörter in Anführungszeichen“ suchen exakt; `-wort` schließt aus |
| **Tippfehler** | Gibt es keine Treffer, kommt ein Vorschlag „Meintest du ‚Zahnarzt‘?“ (Abgleich mit lokal bekannten Titeln per Trigramm-Ähnlichkeit) |
| **Filter-Chips** | Kalender (Mehrfachauswahl), Zeitraum (Demnächst / Vergangen / Diese Woche / Dieser Monat / Eigener Zeitraum), nur Serien, mit Erinnerung, von Ida angelegt oder geändert, ganztägig, mit Anhang, Status (vorläufig/abgesagt), im Papierkorb |
| **Natürliche Sprache** | „zahnarzt nächste woche“, „mathe im november“, „termine am freitag“, „was ist am 12.11.“: Der lokale Parser (`parse.py`) erkennt Zeitangaben und setzt sie als Filter-Chip, den man wegtippen kann |
| **Sprungsuche nach Datum** | „12.11.“ oder „nächsten montag“ zeigt oben den Treffer „Springe zu Do, 12. Nov.“ |
| **Ergebnisliste** | gruppiert in „Demnächst“ (aufsteigend) und „Vergangen“ (absteigend), darin Monats-Überschriften. Das Suchwort ist hervorgehoben. Bei Serien steht der nächste Termin, dazu „+ 23 weitere“. |
| **Leeres Suchfeld** | zuletzt gesucht (lokal, löschbar), häufige Titel als Chips und „Nächste Termine“ |
| **Treffer antippen** | öffnet das Detail-Sheet. „Im Kalender zeigen“ springt in die Tagesansicht und hebt den Termin hervor. |
| **Offline** | Die Suche läuft dann über den IndexedDB-Spiegel (±3 Monate plus alle Serien), mit dem Hinweis „Offline – nur Termine von Aug. bis Jan.“ |
| **Ida** | das Tool `calendar_search` mit denselben Filtern. Unter den Treffern steht „Ida fragen: ‚Wann war ich zuletzt beim Zahnarzt?‘“, das übergibt die Frage an Ida |
| **Tastatur (PC)** | ↑/↓ wählt einen Treffer, Enter öffnet, Esc schließt |

### 8.3 Technik
* **Server:**
  * SQLite FTS5 mit `unicode61 remove_diacritics 2` und einer eigenen Normalisierung (ä → ae, ß → ss), Präfix-Index.
  * Die Treffer werden mit `bm25` sortiert und dann nach Datum gruppiert.
  * Serien werden für den Zeitraum aufgelöst: der nächste und der letzte Termin plus die Gesamtzahl.
* **`GET /api/v1/search`:** `q`, `calendars`, `from`, `to`, `flags` (recurring, alarm, by_agent, all_day, attachment, trash), `limit`/`cursor`.
* **Tippfehler-Vorschläge:** über eine Trigramm-Tabelle aller bisherigen Titel (wenige tausend Einträge, lokal).

---

## 9. Vollständigkeits-Check – ist wirklich alles da?

Abgleich mit dem, was Google Kalender, Samsung Kalender, Apple Kalender und Fantastical können.
Legende: ✅ war schon im Plan · ➕ neu ergänzt · ⏳ später (Phase 6) · ❌ bewusst nicht, mit Grund.

### Ansichten und Navigation
| Funktion | Status |
|---|---|
| Tag, 3 Tage, Woche, Monat, Liste, Jahr | ✅ |
| Monat + Tagesliste geteilt (Samsung-Stil) | ➕ Standard am Handy |
| Mini-Monat zum Springen | ✅ |
| Heute-Knopf, Datum direkt anspringen | ✅ (auch über die Suche) |
| Kalenderwochen, Wochenstart einstellbar | ✅ |
| Arbeitszeiten hervorgehoben | ✅ |
| Zweite Zeitzone im Raster | ➕ |
| Wochenenden ausblenden | ➕ |
| Kalender ein- und ausblenden, „nur diesen“ | ✅ / ➕ |
| Helles Design (System folgen) | ➕ (Phase 5; IDA bleibt dunkel) |
| Druckansicht / PDF der Woche oder des Monats | ➕ (Print-CSS, Phase 5) |

### Termine
| Funktion | Status |
|---|---|
| Titel, Zeit, ganztägig, mehrtägig, Ort, Beschreibung, Link | ✅ |
| Wiederholungen inkl. „am 2. Dienstag“, Werktags, Ende nach n Mal | ✅ |
| Serien bearbeiten: dieser / folgende / alle | ✅ |
| Farbe pro Termin | ✅ |
| Mehrere Erinnerungen pro Termin | ✅ |
| Erinnerung schlummern | ➕ |
| Status Beschäftigt / Frei / Vorläufig / Abgesagt | ➕ (Feld `transparency` dazu) |
| Reisezeit vor dem Termin | ➕ (Feld `travel_min`) |
| Checklisten und Markdown in der Beschreibung | ➕ |
| Anhänge (lokal in `/data/files`) | ➕ (Phase 5, Tabelle `attachments`) |
| Duplizieren, Kopieren/Einfügen | ✅ / ➕ |
| Mehrfachauswahl (verschieben, Kalender wechseln, löschen) | ➕ |
| Vorlagen („Fahrstunde 90 Min., Kalender Privat“) | ➕ (Tabelle `templates`) |
| Vorschläge beim Tippen aus früheren Terminen | ➕ |
| Orte-Autovervollständigung (lokal) | ➕ |
| Countdown („noch 12 Tage“) | ➕ |
| Konflikt-Warnung | ✅ |
| Geburtstage mit Alter, Jahrestage | ✅ |
| Feiertage (lokal berechnet, Bundesland) | ✅ |
| Schulferien | ➕ einmalig als `.ics`-Datei importierbar (kein automatischer Download) |
| Papierkorb, Rückgängig, Wiederholen | ✅ / ➕ Wiederholen |
| Verlauf pro Termin | ✅ |

### Eingabe und Bedienung
| Funktion | Status |
|---|---|
| Aufziehen, Verschieben, Verlängern per Ziehen | ✅ |
| Wischen zum Blättern, Pinch-Zoom | ✅ |
| Wisch-Aktionen in der Liste | ➕ |
| Schnelleingabe in natürlicher Sprache | ✅ |
| Tastenkürzel am PC, Rechtsklick-Menü | ✅ / ➕ |
| Barrierefreiheit: Screenreader-Labels, Fokus-Reihenfolge, Kontrast, Schriftgröße | ✅ (ausdrücklich getestet) |

### Suche
| Funktion | Status |
|---|---|
| Volltext, Filter, natürliche Sprache, Tippfehler-Vorschlag, offline | ➕ (§8) |

### Benachrichtigungen
| Funktion | Status |
|---|---|
| In der App (Sheet + Notification) | ✅ |
| In IDA (Infos) | ✅ |
| Bei geschlossener App | ⏳ CalDAV (lokal) oder Web Push als Opt-in |
| Ruhezeiten | ➕ |
| Tägliche Übersicht am Morgen | ➕ als Ida-Routine über `calendar_day_brief` |

### PWA-Fähigkeiten am Handy
| Funktion | Status |
|---|---|
| Installierbar, eigenes Icon, Vollbild | ✅ |
| Offline lesen, offline schreiben | ✅ |
| App-Shortcuts (lange auf das Icon drücken) | ✅ |
| **App-Badge** mit der Zahl der restlichen Termine heute (Badging API) | ➕ |
| **Teilen an den Kalender** (Web Share Target): Text oder `.ics` aus einer anderen App teilen, dann öffnet die Schnelleingabe bzw. der Import | ➕ |
| `.ics`-Dateien mit der App öffnen (File Handling, PC) | ➕ |
| Homescreen-Widget | ❌ Android unterstützt Widgets für PWAs bisher nicht zuverlässig. Ersatz: App-Badge und Shortcuts; mit CalDAV zeigt das System-Widget der Kalender-App die Termine. |

### Ida
| Funktion | Status |
|---|---|
| Lesen, Anlegen, Ändern, Löschen, Serien | ✅ |
| Freie Zeit, Konflikte, Tagesübersicht, Suche | ✅ |
| Rückgängig, Verlauf, Rechte pro Kalender | ✅ |
| „Ida fragen“ aus der App heraus | ✅ |
| Erinnerungen mit Agent-Aufgabe | ✅ |
| Auswertung („Wie viel Zeit ging diese Woche für Schule drauf?“) | ➕ Tool `calendar_stats` |

### Daten
| Funktion | Status |
|---|---|
| ICS-Import und -Export, Abo-Link | ✅ |
| Backups (nächtlich, herunterladbar) | ✅ |
| Wiederherstellen aus einem Backup in der Oberfläche | ➕ |
| CalDAV für die native Android-App | ⏳ |

### Bewusst nicht dabei
| Funktion | Grund |
|---|---|
| Google-, Outlook- oder iCloud-Sync | soll komplett lokal sein |
| Einladungen per Mail, Zu- und Absagen von Teilnehmern | bräuchte einen externen Maildienst. Teilnehmer lassen sich als reine Namen eintragen. |
| Karten, Wetter, Videokonferenz-Erzeugung | externe Dienste. Ein Link-Feld für Meeting-URLs gibt es. |
| Kalender mit anderen Personen teilen | Einzelnutzer-System. Lesen geht über den Abo-Link. |

### Ergänzungen am Datenmodell (aus dem Check)
* **`events`:**
  * `transparency` (`busy` \| `free`)
  * `travel_min` (Int)
  * `birth_year` (Int?, für Geburtstage)
  * `attendees` (JSON, nur Namen)
  * `url`
* **`templates`:** `id`, `name`, `data` (JSON mit Termin-Feldern), `position`.
* **`attachments`:** `id`, `event_id`, `name`, `mime`, `size`, `path` (Dateien liegen unter `/data/files/`, sie sind in den Backups enthalten).
* **`search_terms`:** Trigramme der Titel für die Tippfehler-Vorschläge (wird aus `events` neu aufgebaut).
* **Neue Hub-Tools:** `calendar_stats` (Zeit pro Kalender oder Stichwort in einem Zeitraum) und `calendar_templates` (Vorlagen auflisten und benutzen).

---

## 10. Frontend – Aufbau

Kein Build-Schritt, Vanilla-ES-Module wie in IDA. Die Oberfläche liegt **einmal** in `frontend/kalender/` und wird vom Kalender-Dienst ausgeliefert. IDA lädt sie über den Proxy.

```
frontend/
  css/tokens.css, css/components.css     aus ida.css herausgelöst (IDA + Kalender)
  fonts/                                 Google Sans Flex, Material Symbols (lokal)
  kalender/
    index.html          eigene Hülle für den Kalender-Port (Kopfzeile, Drawer, FAB)
    main.js             Router (/, /tag/2026-10-29, /woche/…, /monat/…, /termin/<id>), PWA, mount
    sw.js               Service Worker (Offline-Lesen)
    manifest.webmanifest, icons/
    css/calendar.css    Raster, Termin-Blöcke, Ansichten
    js/
      api.js            Aufrufe mit konfigurierbarer Basis ("/api/v1" oder "/kal/api/v1"), SSE mit Neuverbinden
      store.js          sichtbarer Bereich, Cache der Vorkommen, Kalender, Einstellungen
      offline.js        IndexedDB-Spiegel ±3 Monate, Warteschlange für Offline-Änderungen
      dates.js          Wochen, Monate, KW, Wochenstart Mo, Formatierung de-DE
      layout.js         Überlappungs-Spalten, mehrtägige Balken
      views/month.js, week.js (auch Tag/3 Tage), agenda.js, year.js
      sheet-detail.js, sheet-editor.js, recurrence-ui.js, quick-add.js
      gestures.js       Wischen, Ziehen, Aufziehen, Pinch
      app.js            mount(container, {apiBase, embedded}) – rendert alles
frontend/js/pages/calendar.js            IDA-Seite: import("/kal/s/<v>/js/app.js") → mount im #page
```

* **In IDA:**
  * Menüpunkt **„Kalender“** (`calendar_month`) zwischen Start und Chats.
  * Routen `/kalender`, `/kalender/:ansicht/:datum` und `/kalender/termin/:id`.
  * Mit `embedded: true` blendet die Oberfläche ihre eigene Kopfzeile und ihren Drawer aus und nutzt die von IDA.
* **„Ida fragen“** in der Kalender-App (Phase 5) ist eine eingeklappte Leiste.
  * Der Text geht an `POST /api/v1/ask` im Kalender-Dienst, der ihn an IDA weiterreicht (`IDA_INTERNAL_URL`).
  * Ida antwortet und benutzt dabei die calendar_*-Tools.
  * Das ist die einzige Stelle, an der der Kalender IDA aufruft. Ohne IDA ist der Knopf ausgeblendet.

---

## 11. PWA

* **Manifest:**
  * `name: "IDA Kalender"`, `short_name: "Kalender"`, `id: "/kalender-app"`
  * `display: standalone`, `start_url: "/"`, `scope: "/"`
  * `theme_color` und `background_color` aus `tokens.css`
  * `shortcuts`: Neuer Termin → `/?neu=1`, Heute, Woche
  * `display_override: ["window-controls-overlay"]` für den PC
  * Icons im IDA-Stil (Kalenderblatt mit Maskottchen-Augen), maskable, 192 und 512 px
* **Service Worker:**
  * Die App-Hülle (versionierte `/s/<v>/…`-Dateien) wird beim Installieren gecacht.
  * Termine von ±3 Monaten liegen in **IndexedDB** und sind offline lesbar, mit dem Hinweis „Offline – Stand 14:02“.
* **Offline schreiben (umgesetzt, Details in `docs/KALENDER-OFFLINE.md`):** Änderungen landen in einer Outbox in IndexedDB, sind sofort sichtbar (Wolken-Symbol) und werden beim nächsten Internet der Reihe nach, nie doppelt und ohne Datenverlust gesendet; bei Konflikten fragt ein Dialog nach. Die ältere Beschreibung darunter bleibt als Hintergrund stehen:
* **Offline schreiben:**
  * Änderungen kommen in eine Warteschlange und werden beim Wiederverbinden gesendet.
  * Bei einem Konflikt (`updated_at` ist neuer) erscheint ein Dialog: „Meine Version / Server-Version“.
* **Installieren:** wie in `ui/pwa.js` (`beforeinstallprompt`, Knopf im Drawer). Am iPhone: Teilen → Zum Home-Bildschirm.
  Installierbar ist die App nur über HTTPS, also über den Tunnel. Im LAN unter `http://<ip>:8082` ist sie benutzbar, aber nicht installierbar (Browser-Regel).
* **Benachrichtigungen:**
  * **Standard, rein lokal:** Snackbar und Notification bei offener App, außerdem die IDA-Infos über den Webhook.
  * **Optional Web Push (Phase 6, standardmäßig aus):** Damit klingelt das Handy auch bei geschlossener App.
    **Wichtiger Haken:** Web Push läuft technisch immer über den Push-Dienst des Browsers, bei Chrome und Android also über Googles FCM.
    Der Inhalt ist zwar Ende-zu-Ende verschlüsselt, aber die Nachricht verlässt den Server. Deshalb ist Web Push nur als Opt-in gedacht.
    Die Alternative ohne Google ist CalDAV mit der nativen Kalender-App (§9). Dann erinnert das Handy selbst.

---

## 12. Daten rein und raus (alles lokal)

| Weg | Phase | Wie |
|---|---|---|
| **ICS-Import** | 2 | Eine `.ics`-Datei hochladen (Oberfläche oder Chat), Zielkalender wählen; Duplikate werden über die `UID` erkannt. So lassen sich auch alte Termine einmalig übernehmen, als Datei-Export aus jedem Kalender. |
| **ICS-Export** | 2 | pro Kalender oder alles, als Download |
| **Abo-Link** | 4 | `https://kalender.…/ics/<token>.ics`, nur lesen, Token rotierbar. Am Android-Handy z. B. mit **ICSx⁵** (Open Source, holt direkt vom eigenen Server). |
| **Backups** | 1 | nächtlich `kalender.db` (über `VACUUM INTO`) plus ICS-Export nach `/data/backups/`, 14 Stände; in den Einstellungen herunterladbar |
| **CalDAV** | 6 (optional) | Damit die **native Android-Kalender-App** über **DAVx⁵** in beide Richtungen schreibt und das Handy selbst erinnert. Minimaler Server unter `/dav/` (PROPFIND, REPORT calendar-query/multiget, PUT/DELETE mit ETags, ctag). Pfad mit eigenem Passwort, in Cloudflare per Bypass, weil DAVx⁵ kein Access-Login kann. |

---

## 13. Docker / Betrieb

### `docker-compose.yml` – neuer Dienst
```yaml
  # Kalender – eigener Dienst, eigene Daten (SQLite im Volume), eigener Port, eigene PWA
  kalender:
    image: ${KALENDER_IMAGE:-ghcr.io/l8tenever/ida-kalender:latest}
    pull_policy: always
    restart: unless-stopped
    environment:
      TIMEZONE: ${TIMEZONE:-Europe/Berlin}
      KALENDER_PUBLIC_URL: ${KALENDER_PUBLIC_URL:-}
      KALENDER_IDA_TOKEN: ${KALENDER_IDA_TOKEN}
      KALENDER_MCP_TOKEN: ${KALENDER_MCP_TOKEN:-}
      IDA_WEBHOOK_URL: http://app:8000/api/v1/ext/kalender
      IDA_INTERNAL_URL: http://app:8000
    volumes:
      - kalender_data:/data
    ports:
      - "${KALENDER_PORT:-8082}:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/v1/health')"]
    labels:
      com.centurylinklabs.watchtower.enable: "true"

  app:
    environment:
      KALENDER_URL: http://kalender:8000      # neu
      KALENDER_IDA_TOKEN: ${KALENDER_IDA_TOKEN}

volumes:
  kalender_data:
```
* Der Dienst ist **immer an**, ohne Profil, „direkt im Stack mit drin“.
* Kein `depends_on`, in keine Richtung: Beide starten unabhängig. Fehlt der andere Dienst, kommt nur eine klare Fehlermeldung.
* Der Port `8082` ist frei (8080 ist IDA, 8081 das Wiki).

### `.env.example`
```
# Kalender (eigener Dienst, eigene Daten)
KALENDER_IDA_TOKEN=<zufällig, z. B. openssl rand -hex 32>   # IDA ↔ Kalender, nur intern
# KALENDER_PORT=8082                  # oder 10.7.0.1:8442, wenn nur der Tunnel drauf soll
# KALENDER_PUBLIC_URL=https://kalender.example.com
# KALENDER_MCP_TOKEN=<zufällig>       # nur wenn das externe MCP unter /mcp gewünscht ist
```

### CI
`.github/workflows/docker-publish.yml` bekommt einen zweiten Job, ein Matrix-Eintrag reicht:
* Er baut `kalender/Dockerfile` mit dem Repo-Root als Kontext (wegen `frontend/`) und lädt das Image nach `ghcr.io/l8tenever/ida-kalender` hoch.
* Vorher laufen die Tests aus `kalender/tests`.
* Watchtower aktualisiert beide Images.

### Extern erreichbar
* **Cloudflare Access zuerst:** eine App für `kalender.<domain>`, mit derselben Policy wie IDA.
  * **Bypass** gibt es nur für `/ics/*` (Token im Pfad), `/mcp` (Bearer) und später `/dav/*` (eigenes Passwort).
  * Das Manifest wird mit `crossorigin="use-credentials"` geladen, wie bei IDA schon gelöst.
* **Danach der Tunnel:** Hostname `kalender.<domain>` → `http://<server>:8082`.
  * **Achtung:** `tunnel_konfiguration_setzen` löscht das `warp-routing`. Die Ingress-Regel deshalb über die API im MCP-Container ändern.
  * Durch diese Reihenfolge ist der Hostname nie ungeschützt online.

---

## 14. Tests

**`kalender/tests/` (eigene Suite, SQLite im Temp-Ordner):**
* **CRUD und Verlauf:** CRUD, Papierkorb, Wiederherstellen, Rückgängig über `changes`, `by`/`via` richtig gesetzt.
* **Wiederholungen:**
  * wöchentlich über die Zeitumstellung (letzter Sonntag im Oktober und im März)
  * monatlich am 31.
  * `UNTIL` und `COUNT`, `exdates`
  * Scopes `this` / `following` / `all`
  * mehrtägig ganztägig (exklusives Ende)
  * Geburtstag am 29.02.
* **Abfragen:** Bereich an den Grenzen (über Mitternacht, über den Monatswechsel), Freie Zeit, Konflikte, FTS-Suche.
* **Rechte:**
  * `agent_access=read` blockt das Schreiben mit dem `ida`-Token, `none` versteckt den Kalender.
  * Die Oberfläche ist davon nicht betroffen.
  * Ohne Token gibt es keinen Zugriff auf die Agent-Endpunkte, ein falscher Feed-Token liefert 404.
* **Erinnerungen:**
  * Jede feuert genau einmal, auch bei Serien und doppeltem Tick.
  * Der Webhook versucht es erneut, wenn IDA nicht erreichbar ist (Fake-Server).
* **ICS:** Export → Import ergibt dieselben Termine (Round-Trip); fremde Dateien mit TZID und RRULE werden korrekt gelesen.
* **Schnelleingabe:** „morgen 15 Uhr Zahnarzt“, „jeden Mo 8:00 Mathe bis 20.12.“, „Fr ganztägig Urlaub“.
* **Feiertage:** Bundesland-abhängig, nur lesen.
* **Backup:** Datei entsteht und lässt sich öffnen; es bleiben höchstens 14 Stände.
* **App:** Manifest gültig, statische Dateien versioniert, `/mcp` ohne Token liefert 401.
* **Keine ausgehenden Verbindungen:** Ein Test blockiert `socket.connect` nach außen und prüft, dass trotzdem alles läuft.
* **Palette:** Kontrast ≥ 4.5:1.

**`backend/tests/` (IDA):**
* Hub-Tools gegen einen Fake-Kalender-Dienst: Ortszeit, `X-Kalender-By`, Fehlermeldung, wenn der Dienst nicht erreichbar ist.
* Der Proxy streamt SSE.
* Der Webhook erzeugt eine Info bzw. bei `alarm_action=agent` einen Agent-Lauf.
* Die Startseite bekommt ihre Termine aus dem Kalender.

**Frontend:** manueller Check im Browser, in Handy-Breite und am PC:
* Ziehen und Aufziehen
* Serien-Dialog
* Offline-Anzeige und Offline-Warteschlange
* Installieren
* eingebettet in IDA

---

## 15. Reihenfolge (Phasen, je ein Commit und Push)

| Phase | Inhalt | Ergebnis |
|---|---|---|
| **1 – Kalender-Dienst** | `kalender/` mit Modellen, Migration, Services (store, recurrence, query, alarms, backup), REST-API, Tokens, Tests; Dockerfile, CI-Job, Compose-Dienst und Volume, `.env.example` | Ein lokaler Kalender läuft auf `:8082` (zunächst API), Daten liegen separat, nächtliches Backup |
| **2 – Ida-Anbindung** | `kalender_client.py`, `calendar_*`-Tools in `hub.py`, Prompt-Hinweis und 24-h-Kontext, Webhook → Infos/Agent-Lauf, Startseite aus dem Kalender, ICS-Import/-Export, Termin-Karte im Chat | Ida kann Termine anlegen, lesen, ändern und löschen; Erinnerungen kommen in IDA an |
| **3 – Oberfläche** | `tokens.css`/`components.css`, lokale Schriften (auch für IDA), `frontend/kalender/` nach §7: Grundgerüst mit Floating Toolbar und FAB, „Monat + Tag“, Liste mit Wisch-Aktionen, Tag/3 Tage/Woche mit Ziehen, Detail-Sheet, Editor mit Vorschlägen und Konflikt-Hinweis, Serien-Dialog, Suche nach §8, Drawer, Einstellungen, Papierkorb, Verlauf, alle Zustände aus 7.12; SSE-Live-Updates | Voller Kalender auf `:8082` im IDA-Design |
| **4 – In IDA + PWA + extern** | Proxy `/kal/*`, IDA-Seite `/kalender` mit Menüpunkt; Manifest, Service Worker mit Offline-Lesen und Offline-Suche, Icons, App-Badge, Share Target, Shortcuts; Abo-Link mit QR-Code; Cloudflare Access + Hostname | Kalender in IDA **und** als installierbare App unter `kalender.<domain>` |
| **5 – Komfort** | Schnelleingabe (Regel-Parser), „Ida fragen“-Leiste, Jahresansicht, Geburtstage mit Alter, Feiertage, Offline-Schreiben, Agent-Erinnerungen (`alarm_action=agent`), Vorlagen, Anhänge, Mehrfachauswahl, Reisezeit, zweite Zeitzone, helles Design, Druckansicht, `calendar_stats`, Backup-Wiederherstellung, optional `/mcp` | Ida plant aktiv mit, die App ist komplett |
| **6 – optional** | CalDAV für die Android-Kalender-App (DAVx⁵); Web Push als Opt-in; Aufgaben-Ebene (Erinnerungen und Tracker aus IDA im Kalender anzeigen) | Erinnerungen direkt am Handy |

Neue Abhängigkeiten im Kalender-Image: `python-dateutil`, `icalendar`, `holidays`, `aiosqlite`. Alle sind reines Python und arbeiten offline.
IDA selbst braucht nur `httpx`, das es schon hat.

---

## 16. Offene Entscheidungen

1. **Port und Hostname:** `8082` und `kalender.<domain>`, passt das?
2. **Speicher:** SQLite-Datei im eigenen Volume (Vorschlag) oder eine eigene Postgres-Datenbank?
3. **Agents löschen ohne Rückfrage**, weil es Papierkorb und Rückgängig gibt (Vorschlag), oder nur nach Bestätigung im Chat?
4. **Benachrichtigungen am Handy:** Reichen lokal IDA-Infos und die offene App? Oder später Web Push als Opt-in, obwohl es über den Push-Dienst des Browsers läuft? Oder gleich CalDAV mit der nativen Kalender-App als ganz lokale Lösung?
