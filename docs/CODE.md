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
