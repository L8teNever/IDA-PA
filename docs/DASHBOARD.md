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
  * **Adressen, Netze und Daten** je Container: Image, öffentliche URLs, Ports, Netzwerke mit IP und alle Mounts (Volume oder
    Ordner, Zielpfad, nur lesen). Die URLs liest das Dashboard bei `cloudflared` (Metrics-Port, `/config`, kein Token nötig) und
    ordnet sie über den Port dem Container zu. Dafür tritt es bei Bedarf selbst dem Netz von `cloudflared` bei.
    Neben jeder URL steht, ob sie **mit Cloudflare Zero Trust geschützt** (grün, „Zero Trust“) oder **für jeden offen** ist
    (gelb, „öffentlich“). Mit `CLOUDFLARE_API_TOKEN` (derselbe Token wie beim Cloudflare-MCP, siehe unten) liest das Dashboard
    dafür die **Access-Apps samt Policies** aus der Cloudflare-API: Wildcards (`*.example.com`), nur geschützte Pfade („Offen – nur
    /admin ist geschützt“) und Bypass-Policies (zählen als offen) werden erkannt, der Tooltip nennt App und Policies. Zusätzlich
    kommen die Routen der remote verwalteten Tunnel aus der API dazu. Ohne Token oder wenn die API nicht antwortet, ruft das Dashboard
    die Adresse einmal ohne Anmeldung auf (Umleitung zum Access-Login = geschützt; ein eigener Login der App zählt als „öffentlich“,
    nicht erreichbar = „Schutz unbekannt“). Ergebnisse gelten 2 bzw. 5 Minuten (`app/cloudflare_api.py`, `app/access.py`).
    `GET /api/v1/cloudflare` zeigt, ob die API geht (Konto, Zahl der Access-Apps und Tunnel-Routen, Fehler).
  * Verbrauch des Stacks in den letzten 24 Stunden.
* **Dateien**: Datei-Explorer für den ganzen Server (`/` ist unter `/hostfs` eingebunden, mit Schreibrecht). Ordner ansehen
  (Liste oder Kacheln mit Bildvorschau, sortieren, suchen, versteckte Dateien), Bilder, Videos, Audio, PDF und HTML ansehen,
  Textdateien im Code-Editor bearbeiten (CodeMirror, lokal gebündelt unter `frontend/dashboard/vendor/`, neu bauen mit
  `dashboard/editor`), hochladen (viele Dateien und ganze Ordner, in 32-MB-Stücken wegen Cloudflare), ZIP/TAR entpacken, als ZIP
  packen, umbenennen, kopieren, verschieben (auch per Ziehen) und löschen. Am PC: Klick wählt, Doppelklick öffnet, Strg/Shift
  für mehrere, Rechtsklick fürs Menü, Strg+C/X/V, Entf, F2. Am Handy: Tippen öffnet, langes Drücken wählt aus.
  Schutz: `/`, Ordner direkt darunter (`/etc`, `/var` …) lassen sich nicht löschen oder verschieben, `/proc`, `/sys` und `/dev`
  nur ansehen. Symlinks löst das Dashboard selbst auf, damit sie auf dem Server landen und nicht im Container. Entpacken
  lässt nichts aus dem Zielordner heraus. Neues gehört dem Besitzer des Ordners darüber. Fehlt der Mount, zeigt das Tool nur `/opt`.
* **Leistung**: CPU, RAM und Netz des Servers als Diagramm. Zeitraum von 1 Std bis 1 Jahr, vor- und zurückblättern,
  einen Zeitpunkt wählen oder mit der Maus einen Bereich **aufziehen, um hineinzuzoomen**. Dazu kommen die übertragene
  Datenmenge im Zeitraum und der **Verbrauch je Stack oder Container** (Diagramm der größten 6 plus sortierbare Tabelle;
  Klick auf eine Zeile zeigt den Verlauf).
* **Speicher**: Platte (Docker / Stack-Ordner / Sonstiges / frei), Verlauf der Belegung, Docker-Anteile (Images, Volumes,
  Container, Build-Cache), Platz **je Stack** (Images, Volumes, Ordner, Container) und Listen aller Volumes und Images.
  Ungenutzte Images und den Build-Cache kannst du dort aufräumen.

* **Für IDA Code** (intern, `DASHBOARD_URL` in IDA): `POST /api/v1/containers/{id}/exec` (`{cmd, timeout, user}`, Befehl im Container,
  Ausgabe am Ende) und `GET /api/v1/jobs/{id}` (Stand eines Vorgangs mit den letzten Zeilen). Damit bedienen Code-Threads den
  Server-Stack ihres Projekts, siehe docs/CODE.md. Das Dashboard hat keine eigene Anmeldung: den Host-Port (`DASHBOARD_PORT`) nicht ins
  Netz geben, nur über Cloudflare Access.

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
   DASHBOARD_PUBLIC_URL=https://dashboard.kulbarts.com
   # STACKS_ROOT=/opt                    # Ordner, unter dem alle Stack-Ordner liegen
   # DASHBOARD_STACKS_DIRS=/opt/stacks   # hier wird nach Stacks gesucht, die gerade aus sind
   ```
2. **Cloudflare Tunnel**: `dashboard.kulbarts.com` → `http://dashboard:8000` (bzw. Host-Port `8085`).
3. **Cloudflare Access**: `dashboard.kulbarts.com/*` → `Allow` (nur dein Login). **Pflicht**: Das Dashboard hat den
   Docker-Socket, damit hat es volle Kontrolle über den Server. Eine eigene Anmeldung gibt es (wie bei den anderen Apps) nicht.
   Ändernde Aufrufe nimmt es nur als JSON an, damit fremde Webseiten nichts auslösen können.
4. `docker compose pull && docker compose up -d`

### Cloudflare (MCP + API fürs Dashboard)

Der Cloudflare-MCP ([Ida-Cloudflare](https://github.com/L8teNever/Ida-Cloudflare)) läuft als Dienst `cloudflare` im IDA-Stack.
Er und das Dashboard nutzen **einen** Token aus der `.env`:

```ini
COMPOSE_PROFILES=cloudflare            # startet den Dienst mit (z. B. cloudflare,autoupdate)
CLOUDFLARE_API_TOKEN=…                 # Account- oder User-Token, NICHT der Global API Key
CLOUDFLARE_MCP_TOKEN=…                 # Bearer für den MCP-Endpunkt: openssl rand -hex 32
# CLOUDFLARE_MCP_PORT=10.7.0.1:8030    # wenn der Tunnel wie bisher auf 10.7.0.1:8030 zeigt (Standard 127.0.0.1:8030)
```

Rechte des Tokens: wie im README von Ida-Cloudflare (DNS, Zone Settings, Cache Purge, Firewall Services, Cloudflare Tunnel) plus
**Access: Apps and Policies – Read**, damit das Dashboard den Schutz lesen kann. Das Dashboard schreibt nie etwas bei Cloudflare.
In IDA erreicht der MCP intern `http://cloudflare:8030/mcp` (Header `Authorization: Bearer <CLOUDFLARE_MCP_TOKEN>`).
Lief Ida-Cloudflare vorher als eigener Stack (Dockge), den alten Stack stoppen, sonst ist der Port doppelt belegt.

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
  tools/          ein Modul je Tool: stacks.py, files.py, monitor.py, storage.py
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
