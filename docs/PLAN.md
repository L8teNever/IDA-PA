# IDA – Umbauplan (Node-Prototyp → Docker + Python + Datenbank)

## 1. Was es heute gibt (Node-Prototyp, liegt danach unter `legacy/node-app/`)

| Bereich | Heute | Speicherort |
|---|---|---|
| Chat | Claude-Code-CLI wird pro Nachricht gestartet (`claude -p --output-format stream-json`), Antwort wird gestreamt, Session wird fortgesetzt | Verlauf nur im Browser (localStorage) |
| Accounts | mehrere Claude-Logins = mehrere Config-Ordner, Limits über `/api/oauth/usage` | `accounts.json` |
| MCP-Connectoren | URL (+Token), Tool-Liste wird geladen, Tools an/aus | `connectors.json`, `tools.json` |
| Agenten | Haupt-Agent (= Chat) + Sub-Agenten mit Rolle, Persönlichkeit, Anweisungen, Modell, Account, Tool-Rechten, Notizen, Skills, Gedächtnis/Fallback an/aus | `agents.json`, `main-profile.json` |
| Agenten-Hub | internes MCP (`/mcp/<token>`): agent_send/delegate, notify_user, inbox, notes, routine_*, agent_*, skill_*, memory_* – Rechte je nach Aufrufer | im Speicher |
| Nachrichten | Agent ↔ Agent, Benachrichtigungen an den Nutzer, Chats mit Sub-Agenten (Wecken über Warteschlange) | `messages.json` |
| Routinen | Zeitplan **oder** Auslöser (MCP-Tool wird ohne KI abgefragt, „neue Einträge“ → Lauf), Limit-Fallback auf anderen Account, Verlauf | `routines.json` |
| Skills | `/name` im Chat, dauerhaft pro Agent | `skills.json` |
| Gedächtnis | eigenes / gemeinsames / fremdes (nur Haupt-Agent), Hybrid-Suche BM25 + Vektoren (Ollama), Ablauf, Aufräumen per KI + Undo | `memory.db` (SQLite) |
| Warteschlange | alle Hintergrund-Läufe strikt nacheinander | im Speicher |

Probleme: lauter JSON-Dateien, Chats nur im Browser, ein 1000-Zeilen-`server.js`, eine 1000-Zeilen-`index.html`, keine Anmeldung (nur 127.0.0.1), keine URLs pro Seite, kein Deployment.

## 2. Ziel

* **Docker + Docker Compose**: `docker compose up -d` reicht. Das Image kommt **immer von GitHub** (GHCR, `pull_policy: always`), gebaut von einer GitHub Action bei jedem Push auf `main`. Optional automatisches Update (Watchtower-Profil).
* **Python-Backend** (FastAPI, async) liefert API **und** Webseite aus.
* **Datenbank** (PostgreSQL im Compose; SQLite lokal für Entwicklung/Tests) mit sauberen Tabellen und **Alembic-Migrationen** – alles, was heute in JSON-Dateien und im Browser liegt, kommt in die Datenbank.
* **Design** aus `ida-home.html` (Material 3 Expressive, dunkel, Maskottchen, Agenten-Farben, Drawer, Sheets) – echte Daten statt Beispieldaten.
* **Saubere URLs** für jede Seite (History-API, Server liefert für jede Seiten-URL die App aus).
* **Anmeldung** mit Passwort (der Server ist jetzt im Netz erreichbar und kann über MCPs Mails senden usw.).
* **Struktur**, die sich leicht erweitern lässt: ein Ordner pro Schicht (Modelle, Schemas, API-Router, Services), ein Modul pro Fachbereich, Frontend als ES-Module (eine Datei pro Seite).

## 3. Architektur

```
Browser ──HTTPS/HTTP──> app (FastAPI, Uvicorn)
                          ├─ /api/v1/*        REST + Streaming (NDJSON) für den Chat
                          ├─ /mcp/{token}     internes MCP für laufende Agenten (nur intern genutzt)
                          ├─ /static/*        CSS/JS
                          └─ /* (Seiten)      index.html → Router im Browser
                          ├─ Claude-Code-CLI  (im Image installiert, pro Lauf ein Prozess)
                          ├─ Job-Queue        (Hintergrund-Läufe nacheinander)
                          └─ Scheduler        (Zeitpläne + Auslöser-Prüfungen)
                        db (PostgreSQL 16)
                        Ollama (optional, auf dem Host) → Embeddings fürs Gedächtnis
```

### Ordnerstruktur

```
.github/workflows/docker-publish.yml   Tests + Image bauen + nach GHCR pushen
docker-compose.yml                      Betrieb (Image von GitHub)
docker-compose.dev.yml                  lokal selbst bauen
Dockerfile
.env.example
README.md
docs/PLAN.md, docs/ARCHITEKTUR.md
backend/
  requirements.txt, requirements-dev.txt, alembic.ini, pytest.ini
  alembic/                               Migrationen
  app/
    main.py            App-Fabrik, Lifespan (Migrationen, Seed, Scheduler), SPA-Auslieferung
    config.py          Einstellungen aus Umgebungsvariablen
    db.py              Engine, Sessions, Basisklasse
    security.py        WebSocket-Herkunftsprüfung (keine eigene Anmeldung)
    models/            SQLAlchemy-Tabellen, eine Datei je Bereich
    schemas/           Pydantic-Ein-/Ausgaben je Bereich
    api/               Router je Bereich (+ deps.py)
    services/
      claude/          CLI starten, Stream lesen, Prompts bauen, Limits abfragen
      mcp/             Client für Connectoren, internes Agenten-MCP (Tools + Rechte)
      memory/          Speichern, Hybrid-Suche, Embeddings, Aufräumen
      jobs.py          serielle Warteschlange
      scheduler.py     Zeitpläne + Auslöser
      triggers.py      „neue Einträge“-Erkennung
      agents.py        Nachrichten, Wecken, Läufe
      routines.py      Lauf einer Routine (Fallback, Kontext)
      chat.py          Chat-Lauf (Streaming, Speichern)
    seed.py            Start-Daten (Ida, Skills)
    cli.py             `python -m app.cli import-legacy <ordner>`
  tests/
frontend/
  index.html
  css/ida.css          Design aus ida-home.html
  css/app.css          Ergänzungen (Formulare, Listen)
  js/main.js, router.js, api.js, store.js
  js/ui/               dom, sheet, toast, mascot, drawer, composer, model-menu
  js/pages/            home, agent-home, chat, chats, routines, routine-edit, inbox, usage
  js/pages/settings/   index, agents, agent-edit, mcp, mcp-edit, skills, skill-edit, memory, accounts
legacy/node-app/       alter Prototyp (nur noch Referenz / Import)
```

### Datenbank (Tabellen)

| Tabelle | Inhalt |
|---|---|
| `accounts` | Claude-Accounts (Name, Config-Ordner im Volume, optional OAuth-Token) |
| `agents` | alle Agenten inkl. Haupt-Agent (`is_main`), Name, Slug, Farbe, Startseite, Chips, Rolle, Persönlichkeit, Anweisungen, Modell, Account, Fallback, Gedächtnis, an/aus, Notizen |
| `agent_runs` | letzte Läufe je Agent |
| `connectors` | MCP-Server (URL, Header, an/aus, Status, Tool-Liste, abgeschaltete Tools) |
| `agent_connectors` | welcher Agent welchen Connector (und welche Tools) nutzen darf |
| `skills`, `agent_skills` | Skills und welche Agents sie nutzen dürfen |
| `chats`, `chat_messages` | Chats mit allen Nachrichten und Tool-Aufrufen, Claude-Session |
| `messages` | Agent ↔ Agent und Benachrichtigungen an dich (`to = user`) |
| `routines`, `routine_runs` | Routinen (Zeitplan oder Auslöser) und ihr Verlauf |
| `memory_entries`, `memory_backups` | Gedächtnis (Bereich, Art, fest, Wichtigkeit, Ablauf, Nutzung, Embedding) |
| `settings` | Schlüssel/Wert (Standard-Modell, …) |

### URLs

| URL | Seite |
|---|---|
| `/` | Start (Haupt-Agent: Infos + Heute) |
| `/a/{agent}` | Startseite eines Agenten |
| `/chat/{id}` | ein Chat |
| `/chats` | alle Chats (`?q=` Suche) |
| `/nachrichten` | Benachrichtigungen + Agenten-Nachrichten |
| `/routinen`, `/routinen/neu`, `/routinen/{id}` | Routinen |
| `/limits` | KI-Limit aller Accounts |
| `/einstellungen` | Übersicht |
| `/einstellungen/agents`, `/einstellungen/agents/neu`, `/einstellungen/agents/{slug}` | Agents |
| `/einstellungen/mcp`, `/einstellungen/mcp/{id}` | MCP-Server |
| `/einstellungen/skills`, `/einstellungen/skills/neu`, `/einstellungen/skills/{slug}` | Skills |
| `/einstellungen/gedaechtnis` | Gedächtnis |
| `/einstellungen/accounts` | Claude-Accounts |

API: `/api/v1/...` (REST), Chat-Antwort als NDJSON-Stream.

## 4. Wichtige Entscheidungen

* **Claude läuft weiter über die Claude-Code-CLI** (Abo statt API-Key). Im Image sind Node + `@anthropic-ai/claude-code` installiert. Anmelden im Container geht nicht per Browser – stattdessen `.credentials.json` hochladen **oder** einen Token aus `claude setup-token` einfügen.
* **Suche im Gedächtnis** rechnet BM25 + Vektor-Ähnlichkeit + RRF in Python (pro Bereich max. 200 Einträge → schnell genug) – dadurch läuft es auf PostgreSQL und SQLite gleich.
* **Skills** wie bei Anthropic: Agenten sehen Name + Beschreibung der für sie freigegebenen Skills und laden die Anleitung bei Bedarf (`skill_get`); `/skill` im Chat erzwingt einen Skill.
* **Haupt-Agent und Sub-Agenten** liegen in derselben Tabelle; jeder Agent hat seine eigenen Chats (Agent-Auswahl oben rechts wie im Design).
* **Auslöser** (MCP-Abfrage ohne KI) und Tool-Rechte stellt nur der Nutzer ein, nie ein Agent.
* **Anmeldung**: keine eigene – der Zugang wird davor abgesichert (Cloudflare Zero Trust).

## 5. Reihenfolge

1. Gerüst: Ordner, Config, DB, Modelle, Alembic, App-Fabrik, Login.
2. Services: Claude-Runner, MCP-Client, Agenten-Hub, Jobs, Scheduler, Trigger, Gedächtnis, Routinen, Chat.
3. API-Router.
4. Frontend im neuen Design mit Router.
5. Docker, Compose, GitHub Action, README.
6. Import der alten Daten.
7. Tests (pytest mit Fake-Claude), lokal starten, Oberfläche prüfen.
