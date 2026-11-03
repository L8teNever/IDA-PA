# IDA Dashboard – eigene App für den Server

Das Dashboard ist wie Kalender, Wiki und Code eine **eigene App** im IDA-Stack, mit eigener Adresse, eigener PWA und gleichem Look.
Es zeigt alles, was auf dem Server läuft, und lässt es bedienen:

* **Übersicht**: CPU, RAM, Netz und Platte jetzt (mit Verlauf der letzten Stunde), Stacks mit Problemen, größte Verbraucher.
* **Stacks**: findet alle Docker-Stacks des Servers, also Compose-Projekte (über die Labels der Container), einzelne Container und
  Stack-Ordner, die gerade ganz aus sind (Suche in `/opt/stacks`). Mögliche Aktionen:
  * Starten, Stoppen, Neu starten, **Aktualisieren** (`docker compose pull` + `up -d`), Übernehmen (`up -d`), Herunterfahren (`down`).
    Die Ausgabe läuft live mit. Schließt du die Seite, läuft der Vorgang trotzdem zu Ende.
  * **Compose-Datei und `.env` bearbeiten** (Editor mit Zeilennummern, Strg+S). Kaputtes YAML wird abgelehnt. Der alte Stand
    landet vor jedem Speichern unter `data/backups/<stack>/` (die letzten 20). Mit „Speichern & übernehmen“ wird direkt `up -d` ausgeführt.
  * Je Container: Logs (filtern, mitlaufen lassen), Starten, Stoppen, Neu starten, CPU/RAM/Netz jetzt.
  * Verbrauch des Stacks in den letzten 24 Stunden.
* **Leistung**: CPU, RAM und Netz des Servers als Diagramm. Zeitraum von 1 Std bis 1 Jahr, vor- und zurückblättern,
  einen Zeitpunkt wählen oder mit der Maus einen Bereich **aufziehen, um hineinzuzoomen**. Dazu kommen die übertragene
  Datenmenge im Zeitraum und der **Verbrauch je Stack oder Container** (Diagramm der größten 6 plus sortierbare Tabelle;
  Klick auf eine Zeile zeigt den Verlauf).
* **Speicher**: Platte (Docker / Stack-Ordner / Sonstiges / frei), Verlauf der Belegung, Docker-Anteile (Images, Volumes,
  Container, Build-Cache), Platz **je Stack** (Images, Volumes, Ordner, Container) und Listen aller Volumes und Images.
  Ungenutzte Images und den Build-Cache kannst du dort aufräumen.

## Messwerte

Alle 15 s misst das Dashboard den Server (`/proc` vom Host) und jeden laufenden Container (Docker-Stats) und speichert die Werte in
`data/dashboard.db` (SQLite):

| Auflösung | bleibt | Einstellung |
|---|---|---|
| alle 15 s | 48 Std | `SAMPLE_SECONDS`, `RAW_KEEP_HOURS` |
| 10-Minuten-Mittel | 365 Tage | `KEEP_DAYS` |

Die CPU eines Containers wird als **Anteil am ganzen Server** angegeben (alle Kerne = 100 %), damit sie zur Server-CPU passt.
Beim Netz zählen nur echte Netzwerkkarten. `lo`, `veth*`, `br-*`, `docker*`, `wg*` und `tailscale*` fallen raus, sonst würde
Verkehr doppelt gezählt. Mit `DASHBOARD_NET_IFACES=enp0s31f6` legst du die Karten selbst fest.

## Einrichtung

1. In `.env` (siehe `.env.example`):
   ```ini
   DASHBOARD_PUBLIC_URL=https://idaserver.kulbarts.com
   # STACKS_ROOT=/opt                    # Ordner, unter dem alle Stack-Ordner liegen
   # DASHBOARD_STACKS_DIRS=/opt/stacks   # hier wird nach Stacks gesucht, die gerade aus sind
   ```
2. **Cloudflare Tunnel**: `idaserver.kulbarts.com` → `http://dashboard:8000` (bzw. Host-Port `8085`).
3. **Cloudflare Access**: `idaserver.kulbarts.com/*` → `Allow` (nur dein Login). **Pflicht**: Das Dashboard hat den
   Docker-Socket, damit hat es volle Kontrolle über den Server. Eine eigene Anmeldung gibt es (wie bei den anderen Apps) nicht.
   Ändernde Aufrufe nimmt es nur als JSON an, damit fremde Webseiten nichts auslösen können.
4. `docker compose pull && docker compose up -d`

### Warum `/opt` unter demselben Pfad?

Compose merkt sich in den Labels der Container den echten Ordner auf dem Server (z. B. `/opt/stacks/sofia`). Damit das Dashboard
diese Dateien lesen, bearbeiten und `docker compose` dort ausführen kann (relative Pfade wie `./data` müssen auf dem Server
stimmen), wird `STACKS_ROOT` (Standard `/opt`) **unter demselben Pfad** eingebunden. Das deckt `/opt/stacks/*`, `/opt/dockge` und
`/opt/docker/*` ab. Stacks, deren Ordner woanders liegt, erscheinen trotzdem: Starten, Stoppen, Neu starten und Logs gehen,
nur Bearbeiten und Aktualisieren nicht. Die Oberfläche sagt dann, warum.

### Das Dashboard selbst

Das Dashboard erkennt sich am Label `ida.dashboard: "self"`. Aktionen auf den IDA-Stack lassen den Dienst `dashboard` aus
(sonst würde es sich mitten im Vorgang selbst beenden), und „Herunterfahren“ gibt es für diesen Stack nicht. Das Dashboard
selbst wird über Watchtower aktualisiert oder auf dem Server mit `docker compose pull dashboard && docker compose up -d dashboard`.

## Aufbau und Erweitern

```
dashboard/app/
  main.py         App, Auslieferung der PWA, Schutz für ändernde Aufrufe, lädt die Tools
  config.py       Einstellungen aus der Umgebung
  docker_api.py   schlanker Client für die Docker-Engine-API (Socket oder tcp://, z. B. Socket-Proxy)
  discovery.py    Stacks erkennen (Labels, Stack-Ordner), Dateien eines Stacks
  hostinfo.py     CPU/RAM/Netz/Platte des Servers aus /proc
  store.py        Messwerte in SQLite, Verdichtung, Abfragen für Diagramme
  jobs.py         Hintergrund-Vorgänge mit Live-Ausgabe (NDJSON)
  tools/          ein Modul je Tool: stacks.py, monitor.py, storage.py
frontend/dashboard/
  js/app.js       Rahmen (Leiste, Seitenleiste, Kopfzeile, Navigation)
  js/chart.js     Diagramme (SVG, ohne Bibliothek)
  js/tools/       eine Datei je Tool + index.js (Liste der Tools)
```

**Neues Tool:**
1. Backend: `dashboard/app/tools/<name>.py` mit `router = APIRouter()` und `tool = Tool(id=…, name=…, icon=…, router=router,
   start=…, stop=…)` (start/stop optional für Hintergrund-Dienste) und in `tools/__init__.py` → `all_tools()` eintragen.
2. Oberfläche: `frontend/dashboard/js/tools/<name>.js` (Aufbau steht oben in `js/tools/index.js`) und dort in `TOOLS` eintragen.
   Leiste links, Leiste unten am Handy und Navigation kommen automatisch dazu.
3. Tests in `dashboard/tests/` (Fake-Docker in `conftest.py`).

## Entwicklung

```sh
cd dashboard && pip install -r requirements-dev.txt && python -m pytest
DATA_DIR=./data FRONTEND_DIR=../frontend HOST_PROC=/proc HOST_ROOT=/ uvicorn app.main:app --port 8085
```
