# IDA

Persönliche KI-Assistentin mit mehreren Agents (Ida, Stella, Lea …), Chats, Routinen, Auslösern („neue Mail → Routine“), Skills und Langzeit-Gedächtnis.
Läuft als Docker-Compose-Stack: Python-Backend (FastAPI) mit Weboberfläche, PostgreSQL-Datenbank, Claude über die Claude-Code-CLI (Abo, kein API-Key).

## Starten

```bash
git clone https://github.com/L8teNever/IDA-PA.git && cd IDA-PA     # oder nur docker-compose.yml + .env.example kopieren
cp .env.example .env          # APP_PASSWORD und POSTGRES_PASSWORD eintragen
docker compose up -d          # zieht ghcr.io/l8tenever/ida-pa:latest
```

Dann `http://<server>:8080` öffnen und anmelden.

**Hinter Cloudflare Zero Trust:** `CF_ACCESS_TEAM_DOMAIN` und `CF_ACCESS_AUD` (AUD-Tag der Access-App) in der `.env` setzen –
dann prüft IDA das signierte Cloudflare-Token jeder Anfrage und du musst dich nicht zusätzlich mit dem Passwort anmelden.

* **Updates:** `docker compose pull && docker compose up -d` – oder automatisch mit `docker compose --profile autoupdate up -d` (Watchtower prüft alle 10 Minuten).
* **Privates Repo:** einmal `docker login ghcr.io` (GitHub-Token mit `read:packages`), sonst kann das Image nicht geladen werden.
* **Selbst bauen:** `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`

### Claude-Account anmelden

Unter **Einstellungen → Claude-Accounts → „Im Browser anmelden“**: Link öffnen, bei Claude einloggen, den angezeigten Code
in IDA einfügen. Die Claude-CLI speichert den Login selbst im Daten-Volume (inkl. automatischer Erneuerung).
Alternativ: `~/.claude/.credentials.json` hochladen oder einen Token aus `claude setup-token` einfügen.

Mehrere Accounts sind möglich; ist einer am Limit, nehmen Chats, Routinen und Agents automatisch den nächsten.

### Daten vom alten Prototyp übernehmen

```bash
docker compose cp legacy/node-app app:/tmp/legacy
docker compose exec app python -m app.cli import-legacy /tmp/legacy
```

Routinen kommen pausiert an (erst prüfen, dann einschalten).

## Seiten

| URL | Inhalt |
|---|---|
| `/` | Start: Infos (Benachrichtigungen der Agents) und Heute |
| `/a/<agent>` | Startseite eines Agents, Chat mit ihm |
| `/chat/<id>`, `/chats?q=` | Chats |
| `/nachrichten` | Benachrichtigungen, Nachrichten zwischen Agents |
| `/routinen`, `/routinen/neu`, `/routinen/<id>` | Routinen (Zeitplan oder Auslöser) |
| `/limits` | KI-Limit aller Accounts |
| `/einstellungen/agents/<id>`, `/mcp/<id>`, `/skills/<id>`, `/gedaechtnis`, `/accounts` | Einstellungen |

API-Doku: `/api/docs`.

## Aufbau

```
backend/app/
  main.py          App, Start (Migrationen, Seed, Scheduler), Auslieferung der Seiten
  config.py        Einstellungen aus der Umgebung
  db.py, models/   Datenbank und Tabellen (eine Datei je Bereich)
  schemas/         Eingaben der API
  api/             ein Router je Bereich (+ serializers.py für die Ausgaben)
  services/        Logik: claude/ (CLI, Prompts, Limits), mcp/ (Connector-Client, internes Agent-MCP),
                   memory/ (Gedächtnis), agents, routines, triggers, chat, jobs, scheduler, skills
  cli.py           Import alter Daten, Passwort setzen
backend/alembic/   Datenbank-Migrationen
backend/tests/     pytest (startet die echte App mit einem Fake-Claude)
frontend/          index.html, css/ (Design), js/ (Router, API, ui/, pages/ – eine Datei pro Seite)
```

**Erweitern:** neue Tabelle → `models/` + `alembic revision --autogenerate`; neue API → Router in `api/` und in `main.py` eintragen; neue Seite → Datei in `frontend/js/pages/` und Route in `main.js`; neues Agent-Tool → `services/mcp/hub.py`.

## Entwicklung

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Linux/Mac: .venv/bin/pip
APP_PASSWORD=test .venv/Scripts/uvicorn app.main:app --reload               # SQLite unter ./data
.venv/Scripts/python -m pytest
```

Ohne `DATABASE_URL` nutzt die App SQLite unter `data/` – praktisch zum Entwickeln. Im Compose läuft PostgreSQL.
GitHub Actions testet bei jedem Push gegen SQLite **und** PostgreSQL und baut danach das Image.

Details zum Umbau und zu den Entscheidungen: [docs/PLAN.md](docs/PLAN.md).
