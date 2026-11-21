# IDA Health – Geräte mit ida-connect

Health ist wie Dashboard und Tasks eine **eigene App** im IDA-Stack (Dienst `health`, Port 8096, Volume `health-data`), gedacht für
`health.kulbarts.com`. Sie zeigt und bedient alle Geräte, auf denen der Agent **ida-connect** läuft
([github.com/L8teNever/Ida-connect](https://github.com/L8teNever/Ida-connect), Rust, ~1,2 MB):

* **Übersicht**: alle Geräte als Karten, live: online/offline, CPU, RAM, Platte, Temperatur, Netz, Mesh-IP, CPU-Verlauf der letzten Stunde.
* **Gerät → Übersicht**: Kacheln (CPU, RAM, Speicher, Temperatur + Lüfter, Netz, Laufzeit) und Verläufe für CPU (Mittel + Spitze),
  RAM/Swap, Temperatur, Netz, Platten-Durchsatz und Last – 1 Std bis 1 Jahr, Zoom per Ziehen. Darunter je Kern, je Sensor,
  je Laufwerk, je Netzwerkkarte. Solange jemand zuschaut, schickt das Gerät alle 2 s, sonst alle 10 s.
* **Terminal**: Shell als root auf dem Gerät im Browser (xterm.js) – wie SSH, nur über Health.
* **Dateien**: Ordner durchsehen, Textdateien bearbeiten (Strg+S), hoch- und herunterladen (beliebig groß, in Stücken), umbenennen, löschen.
* **Docker**: Compose-Stacks mit ihren Containern – Aktualisieren (`pull` + `up -d`), Neu starten, Stoppen/Starten, Logs,
  Compose-Datei und `.env` bearbeiten.
* **System**: Hardware und System, Updates prüfen und installieren (apt/dnf/apk/pacman), ida-connect aktualisieren, Neustart,
  Cloudflare Mesh einrichten, Name/Notizen, Gerät entfernen, **Protokoll** aller Aktionen (wer, was, wann).

## Wie es zusammenhängt

```
Gerät (ida-connect) ──WebSocket, ausgehend──► health.kulbarts.com/api/v1/agent/ws ──► Dienst health (SQLite)
                                                                                      ▲            ▲
Browser (Cloudflare Access) ── health.kulbarts.com ───────────────────────────────────┘            │
IDA (geraet_*-Tools, intern http://health:8000, Bearer HEALTH_IDA_TOKEN) ──────────────────────────┘
```

Alles läuft über die eine Verbindung, die das Gerät aufbaut: Messwerte, Anfragen (`req`/`res`) und Shell-Sitzungen (`pty_*`).
Kein offener Port am Gerät, klappt hinter NAT und Mobilfunk. Protokoll: [HEALTH-PROTOKOLL.md](HEALTH-PROTOKOLL.md).

**Speicher** (`health.db`, SQLite WAL): eine schmale Zeile je Gerät und Zeitpunkt (`samples`, WITHOUT ROWID, Schlüssel Gerät+Auflösung+Zeit)
mit CPU, CPU-Spitze, RAM, Swap, Last, Temperatur (max. aller Sensoren), Netz, Platten-Durchsatz und -Belegung. Einzelwerte höchstens alle
10 s, 48 Stunden lang; danach 5-Minuten-Mittel (mit Spitzen) für 400 Tage. Geschrieben wird gesammelt alle 5 s in einer Transaktion.
Details (je Kern, Sensor, Laufwerk, Netzwerkkarte) gibt es nur als letzten Stand. Auf dem Gerät wird nichts gespeichert.

## Gerät hinzufügen

1. Auf dem Gerät: `curl -fsSL https://raw.githubusercontent.com/L8teNever/Ida-connect/main/install.sh | sudo sh`
2. `http://<gerät>:1111` öffnen, PIN aus der Installation eingeben, **„Mit IDA verbinden“**.
3. Health öffnet sich (Cloudflare-Anmeldung), Name prüfen, „Ins Cloudflare Mesh aufnehmen“ an/aus, **Verbinden**.

Oder in Health **„Gerät hinzufügen“** → fertiger Befehl mit Einmal-Code (`--code …`, 10 Minuten gültig) – dann ganz ohne Browser.
ida-connect läuft auf dem Gerät als Systemdienst (`ida-connect.service`, systemd, kein Docker). Optionen des Install-Skripts (Update, Entfernen, anderer Port): siehe README von Ida-connect.

## Cloudflare Mesh

Mit Mesh bekommt das Gerät eine eigene IP aus `100.96.0.0/12`; alles, was darauf läuft, ist dann von jedem WARP-Gerät aus erreichbar.
Beim Koppeln (Häkchen) oder unter System → „Einrichten“ legt Health per API einen Mesh-Knoten an
(`POST /accounts/{id}/warp_connector`, Token: `GET …/warp_connector/{id}/token`) und schickt den Token ans Gerät; der Agent installiert
`cloudflare-warp` (apt, Debian/Ubuntu/Raspberry Pi OS) und meldet sich mit `warp-cli connector new <token>` an.

* Braucht `HEALTH_CLOUDFLARE_API_TOKEN` mit **Cloudflare One Connectors Write** (eigener Token, nicht der des Dashboards).
  Ohne Token: in Cloudflare unter Networking → Mesh einen Knoten anlegen und den Token unter System → „Mit eigenem Token“ (oder auf
  der lokalen Seite des Geräts) einfügen.
* Einmal pro Konto muss Mesh in Cloudflare eingeschaltet sein (Assistent unter Networking → Mesh).
* Nur 64-Bit (x86_64, aarch64). 32-Bit-Geräte haben alles andere, aber kein Mesh.
* Gerät entfernen löscht auch den Mesh-Knoten (sofern Health ihn angelegt hat).

### Health selbst im Mesh

Der Container hat WARP (`cloudflare-warp`). Unter **Einstellungen → Cloudflare Mesh → „Health ins Mesh aufnehmen“** legt Health
einen eigenen Knoten `ida-health` an, verbindet WARP damit und schickt seine Adresse `http://<Mesh-IP>:8000` an alle Geräte
(`config.mesh_server`). Geräte, die selbst im Mesh sind, schicken dann alles über das Mesh – sonst oder bei Problemen über die
öffentliche Adresse. Beim Gerät steht unter System „Verbunden über“.

* Braucht im Compose beim Dienst `health`: `cap_add: [NET_ADMIN]`, `devices: [/dev/net/tun]` und
  `sysctls: net.ipv4.conf.all.src_valid_mark=1` (steht so in `docker-compose.yml`). Fehlt das, startet Health normal, nur ohne WARP;
  die Einstellungsseite sagt dann, was fehlt. `HEALTH_WARP=0` schaltet WARP ganz ab.
* Die Registrierung liegt in `/data/warp` (Volume `health-data`) und übersteht Updates.
* **Über die Mesh-IP ist nur der Agent-Pfad erreichbar** (`/api/v1/agent/*`), nicht Oberfläche und API – dort steht kein
  Cloudflare Access davor. Geprüft wird die lokale Adresse der Verbindung, die sich nicht per Header fälschen lässt.
* Feste Adresse statt der automatischen: Einstellung „Adresse von Health im Mesh“.

## Updates von ida-connect

Beim Gerät unter **System → ida-connect**: „Nach Update suchen“ (das Gerät fragt GitHub), „Jetzt installieren“ und Auto-Update
(Standard an, alle 6 Stunden). Auf dem Gerät: `sudo ida-connect update`. Kopplung und Einstellungen bleiben; startet die neue Version
nicht, kommt die alte zurück.

## Einrichten im Stack

`.env` (siehe `.env.example`):

```
HEALTH_PORT=10.7.0.1:8455             # nur für den Tunnel
HEALTH_PUBLIC_URL=https://health.kulbarts.com
HEALTH_IDA_TOKEN=<openssl rand -hex 32>
HEALTH_ACCESS_TEAM=<team>             # aus <team>.cloudflareaccess.com
HEALTH_ACCESS_AUD=<AUD-Tag der Access-App>
HEALTH_CLOUDFLARE_API_TOKEN=          # optional, nur für Mesh
```

Cloudflare (Zero Trust):

1. Tunnel-Route `health.kulbarts.com` → `http://10.7.0.1:8455` (WebSockets sind bei Cloudflare automatisch an).
2. Access-App **health.kulbarts.com** – Policy „Allow“ nur für dich.
3. Zweite Access-App **health.kulbarts.com/api/v1/agent** – Policy **Bypass** (Everyone). Ohne sie bekommt der Agent nur die
   Login-Seite und meldet „Cloudflare Access blockiert den Agent-Pfad“. Dieser Pfad hat eigene Anmeldung: Einmal-Code beim Koppeln,
   danach Geräte-Id + Secret (Health speichert nur SHA-256). Statt Bypass geht auch ein Service-Token (Felder `cf_client_id` /
   `cf_client_secret` in `/etc/ida-connect/config.json`).
4. AUD-Tag der Access-App aus Schritt 2 als `HEALTH_ACCESS_AUD` eintragen – dann prüft Health das Access-JWT selbst
   (Signatur, Aussteller, Zielgruppe) und ein direkter Aufruf am Port hilft niemandem.

Ohne `HEALTH_ACCESS_AUD`, aber mit `HEALTH_IDA_TOKEN`, lässt Health Browser nur durch, wenn Cloudflare Access davor stand (Header des
Tunnels). Ganz ohne beides bleibt Health **zu** (503 „nicht abgesichert“); nur mit `ALLOW_OPEN=1` ist es zum Testen offen.

## Sicherheit

* Die Shell ist root auf dem Gerät. Darum: Oberfläche nur hinter Access, Port nur an die Tunnel-IP binden.
* Browser-WebSockets (Live, Terminal) und Uploads prüfen den `Origin` (gegen fremde Seiten), Koppeln hat einen CSRF-Token.
* Rücksprung beim Koppeln nur zu `http(s)://<gerät>/pair/done` – keine anderen Adressen. Liegt die Seite des Geräts nicht in einem
  privaten Netz (privat, Loopback, CGNAT/Mesh, `.local`/`.lan`), warnt die Bestätigungsseite und Mesh ist nicht vorausgewählt.
* Docker- und Update-Befehle setzt Health selbst zusammen (`shlex.quote`), Aktionen nur aus einer festen Liste.
* Jede Aktion (Befehl, Shell, Datei schreiben/löschen, Docker, Update, Mesh, Koppeln) landet im Protokoll des Geräts.

## IDA und IDA Code

Mit `HEALTH_URL` (Standard `http://health:8000`) und `HEALTH_IDA_TOKEN` bekommt IDA die Tools `geraete_liste`, `geraet_status`,
`geraet_befehl`, `geraet_dateien`, `geraet_datei_lesen`, `geraet_datei_schreiben`, `geraet_docker`, `geraet_docker_aktion`,
`geraet_docker_logs`, `geraet_update` (`services/mcp/device_tools.py`).

* **Ida** (Haupt-Agent) im Chat: alle Geräte. Ändern nur nach klarem Auftrag (steht so in den Tools).
* **Code-Threads**: nur die Geräte, die beim Projekt gewählt sind (Projekt-Menü → **„Geräte wählen“**, gespeichert in
  `code_projects.devices`). Befehle, Schreiben, Docker-Aktionen und Updates brauchen in „Nachfragen“/„Bearbeiten“ deine Freigabe,
  bei „Voller Zugriff“ laufen sie direkt. Der Prompt des Threads nennt die Geräte.
* Stella (Telefon/Live) bekommt die Tools nicht (`VOICE_DENY_TOOLS` enthält `geraet*`).

## API (Auszug)

| | |
|---|---|
| `GET /api/v1/devices` | alle Geräte mit `online`, `info`, `latest` |
| `GET /api/v1/devices/{id oder Name}/history?range=3600` | Verlauf |
| `POST /api/v1/devices/{id}/exec` | `{cmd, timeout, user}` |
| `GET …/fs/list?path=`, `GET …/fs/read?path=`, `GET …/fs/download?path=`, `POST …/fs/write`, `POST …/fs/upload?path=` | Dateien |
| `GET …/docker`, `POST …/docker/stack {name, action}`, `POST …/docker/container`, `GET …/docker/logs` | Docker |
| `GET …/updates`, `POST …/update {target: system\|agent}`, `POST …/mesh {token?}` | System |
| `GET …/agent-update`, `POST …/auto-update {on}` | Update von ida-connect prüfen, Auto-Update am Gerät |
| `GET /api/v1/settings/mesh-self`, `POST …/mesh-self/join`, `POST …/mesh-self/leave` | Health selbst im Mesh (nur Browser) |
| `POST /api/v1/pair-codes {name, mesh}` | Einmal-Code + Install-Befehl |
| `WS /api/v1/live?dev=`, `WS /api/v1/devices/{id}/shell` | Live-Werte, Terminal |
| `WS /api/v1/agent/ws` | die Geräte (Bypass in Access) |
