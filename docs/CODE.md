# IDA Code – eigene App für den Code-Bereich

Der Code-Bereich (Projekte, Threads, Änderungen, Dateien, Vorschau) läuft als **eigene App** mit eigener Adresse und eigener PWA, so wie Kalender und Wiki:

* **Gleiche Arbeit wie in IDA**: KI-Accounts, Modelle, Projekte, Git und die GitHub-Anmeldung bleiben in IDA. Der Dienst `code` hat keine eigenen Daten. Er liefert die Oberfläche aus und reicht die API-Aufrufe intern an IDA (`http://app:8000`) weiter. Antworten werden gestreamt.
* **Nur freigegebene Endpunkte**: `bootstrap`, `usage`, `chat`/`chats/*`, `code/*`, `github/*`, `uploads`, `files`. Alles andere aus IDA ist über die Code-App nicht erreichbar.
* **Gleicher Look wie Kalender und Wiki**: dieselben Design-Dateien (`frontend/css/tokens.css`, `components.css`) und Schriften, Leiste links, Bereich rechts für Änderungen und Dateien.
* **Eigenständige PWA** unter `https://idacode.kulbarts.com` hinter **Cloudflare Zero Trust** (keine eigene Anmeldemaske).

## Einrichtung

1. In `.env` (siehe `.env.example`):
   ```ini
   CODE_PUBLIC_URL=https://idacode.kulbarts.com
   # CODE_PORT=8084
   # IDA_PUBLIC_URL=https://ida.kulbarts.com
   ```
2. **Cloudflare Tunnel**: `idacode.kulbarts.com` → `http://code:8000` (bzw. Host-Port `8084`).
3. **Cloudflare Access**: `idacode.kulbarts.com/*` → `Allow` (dein Login), genau wie beim Kalender und Wiki.
4. `docker compose pull && docker compose up -d`

Ist `CODE_PUBLIC_URL` gesetzt, öffnet „Code“ in der IDA-Seitenleiste die neue App, und alte Links nach `/code/...` leiten dorthin weiter. Ist es leer, bleibt der Code-Bereich wie bisher in IDA.

## Entwicklung

```sh
cd code && pip install -r requirements-dev.txt && python -m pytest
IDA_INTERNAL_URL=http://localhost:8080 FRONTEND_DIR=../frontend uvicorn app.main:app --port 8084
```


## Sandbox

Jeder Code-Thread bekommt eine eigene Sandbox: einen Linux-Container (`node:22-bookworm`) mit Root, Node, Python, git und Internet.
Die KI nutzt sie mit `sandbox_ausfuehren`, der Nutzer im Tab „Sandbox“ der Code-App.

- Der Dienst `sandbox` (`app/sandbox/main.py`) hat als einziger für IDA den Docker-Socket und kennt keine Zugangsdaten (kein `env_file`). Er kann nur Sandbox-Container anlegen.
- Das Projekt kommt als Kopie nach `/workspace`, ohne `.git` und `.env`. Änderungen landen nicht im echten Projekt.
- 2 GB RAM und 2 CPUs. Mehr RAM (bis `SANDBOX_MAX_GB`, Standard 6) fragt die KI mit `sandbox_mehr_ram`, der Nutzer bestätigt im Thread. Das gilt bis zum nächsten Stopp.
- 15 Minuten ohne Befehl: Die Sandbox stoppt samt allem, was darin läuft. Dateien bleiben, nach 7 Tagen ohne Nutzung wird alles gelöscht.
- Eingeschaltet wird sie mit `SANDBOX_SECRET` in der `.env`.
- Netz: Internet ja, Server und lokales Netz nein. Das regelt die Firewall des Servers über die Schnittstelle `ida-sandbox0` (`ida-sandbox-firewall.service`, auf dem Server eingerichtet). Auf einem neuen Server muss sie dort neu eingerichtet werden, sonst erreicht die Sandbox interne Dienste.

## Server-Stack (Docker auf dem Server)

Pro Projekt lässt sich im Projekt-Menü unter **Server-Stack wählen** ein Docker-Stack des Servers einstellen (Liste aus dem
Dashboard). Die KI im Thread bekommt dann diese Tools. Sie gehen über die interne API des Dashboards (`DASHBOARD_URL`,
Standard `http://dashboard:8000`), SSH braucht es dafür nicht:

| Tool | Was es macht | Freigabe |
|---|---|---|
| `server_status` | Dienste, Zustand, Image, Health, öffentliche URLs mit Schutz (Zero Trust / öffentlich) | nie |
| `server_logs` | letzte Zeilen eines Dienstes, optional gefiltert | nie |
| `server_befehl` | Shell-Befehl im laufenden Container (`docker exec … sh -c`), wie ein Terminal | bei „Nachfragen“ und „Bearbeiten“ |
| `server_aktion` | `update` (pull + up -d), `restart`, `start`, `stop`, `up`; wartet, bis der Vorgang fertig ist | bei „Nachfragen“ und „Bearbeiten“ |

„Immer erlauben“ gilt bei Befehlen für alle Befehle in diesem Stack und bei Aktionen je Aktion, jeweils nur in diesem Thread.
Ohne gewählten Stack sagen die Tools das. Das Dashboard selbst nimmt keine Befehle an. Im Prompt steht, welcher Stack zum Projekt gehört.
