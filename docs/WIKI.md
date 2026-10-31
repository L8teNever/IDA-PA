# IDA Wiki – Architektur & Betrieb

Ein **eigenes, vollständiges Markdown-Wiki & KI-Gedächtnis**, das komplett lokal im selben Docker-Stack wie IDA und Kalender läuft:

* **Eigene Speicherung & echte Dateien**: Markdown-Dateien im Dateisystem (`/data/vault/`) kombiniert mit einer eigenen SQLite-Datenbank (`/data/wiki.db` mit FTS5-Volltextsuche, Link-Graph und kompletter Revisionshistorie) im Volume `wiki-data`.
* **Drei klar getrennte Hauptbereiche (`Spaces`)**:
  1. **`privat/`** – Lebenslange persönliche Wissens- & Lebensstruktur (`00-inbox` bis `99-archiv`).
  2. **`memory/`** – Strukturiertes, selbstpflegendes Langzeitgedächtnis für alle IDA-Agents und externe KIs.
  3. **`ki/`** – Automatischer Echtzeit-Spiegel aller IDA-Agents, Chats und Code-Projekt-Threads (inkl. Bildern, PDFs und Textdateien).
* **Obsidian-Funktionalität**: `[[Wikilinks]]` (auch bereichsübergreifend), `![[Datei- & Seiten-Embeds]]`, Callouts, `#Tags`, YAML-Frontmatter, Backlinks, automatisches Link-Umschreiben beim Verschieben, Dateianhänge und interaktiver **Wissensgraph**.
* **Externer MCP-Server (`/mcp`)**: Token-geschützter Streamable-HTTP-MCP-Endpunkt für externe KI-Clients (Cursor, Claude Desktop, T3 Code …). In `memory/` dürfen KIs lesen & strukturiert schreiben; in `privat/` und `ki/` haben externe MCPs **strikt nur Lesezugriff**.
* **IDA-Freigabe für `privat/`**: Über IDA kann nach **expliziter Nutzer-Freigabe** (Inline-Diff-Karte im IDA-Chat bzw. Freigabe-Vorschlag im Wiki) auch im privaten Bereich angelegt oder geändert werden.
* **Eigenständige Android-17-PWA**: Gehostet unter `https://wiki.kulbarts.com` hinter **Cloudflare Zero Trust** (keine eigene Login-Maske), offline-fähig und direkt aus der IDA-Seitenleiste verlinkt.

---

## 1. Einrichtung in `.env` & Cloudflare Zero Trust

1. In `.env` setzen (siehe `.env.example`):
   ```ini
   WIKI_PORT=8083
   WIKI_PUBLIC_URL=https://wiki.kulbarts.com
   WIKI_IDA_TOKEN=<openssl rand -hex 32>
   WIKI_MCP_TOKEN=<openssl rand -hex 32>
   ```
2. **Cloudflare Tunnel**: `wiki.kulbarts.com` → `http://wiki:8000` (bzw. Host-Port `8083`).
3. **Cloudflare Access (Zero Trust)**:
   * **Regel 1 (Oberfläche & PWA)**: `wiki.kulbarts.com/*` → `Allow` (dein Login). Die Oberfläche braucht dadurch keine eigene Anmeldemaske.
   * **Regel 2 (Externer MCP-Endpunkt)**: `wiki.kulbarts.com/mcp*` → `Bypass` (Service-Zugriff ohne Browser-Login; `/mcp` prüft zwingend `Authorization: Bearer <Token>` und weist alles ohne gültigen Token mit `401` ab).

### Externes MCP in anderen KI-Tools eintragen
```json
{
  "mcpServers": {
    "ida-wiki": {
      "type": "http",
      "url": "https://wiki.kulbarts.com/mcp",
      "headers": {
        "Authorization": "Bearer <WIKI_MCP_TOKEN_ODER_EINZELTOKEN>"
      }
    }
  }
}
```
Unter **Einstellungen → MCP-Zugänge** in der Wiki-App können zusätzlich einzelne benannte Tokens (z. B. `cursor`, `claude-desktop`) angelegt, auf Wunsch für `privat/`/`ki/` ausgeblendet und jederzeit widerrufen werden.

---

## 2. Rechte-Matrix (hart im Wiki-Dienst durchgesetzt)

| Bereich | Oberfläche (`user`) | Externes MCP (`/mcp`) | IDA-Agents (`WIKI_IDA_TOKEN`) |
|---|---|---|---|
| **`privat/`** | Lesen & Schreiben | **Nur Lesen** (`403` bei jedem Schreibversuch) | Lesen + **Schreiben nur nach expliziter Nutzer-Freigabe** (Proposal + Diff-Bestätigung) |
| **`memory/`** | Lesen & Schreiben | **Lesen & strukturiert Schreiben** (Pflichtkategorien, Dubletten-Schutz, Secret-Scanner, Auto-`_index.md`) | **Lesen & strukturiert Schreiben** + automatischer System-Prompt-Kontext in IDA |
| **`ki/`** | Lesen, Suchen, Verlinken | **Nur Lesen** | Lesen + automatischer Echtzeit-Spiegel (`system:sync`) aller Agents, Chats & Code-Threads |

---

## 3. Im Stack starten

Wie der Kalender: eigener Container im selben Compose-Stack, eigenes Volume, eigener Port.

```bash
# .env: WIKI_IDA_TOKEN, WIKI_PUBLIC_URL, optional WIKI_MCP_TOKEN setzen
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build wiki
# oder der ganze Stack:
docker compose up -d
```

| Was | Wo |
|---|---|
| Backend + API + MCP | `wiki/` (FastAPI, SQLite `wiki.db`, Vault unter `/data/vault/`) |
| PWA-Oberfläche | `frontend/wiki/` (Material 3 Expressive, Offline IndexedDB) |
| IDA-Anbindung | `WIKI_URL=http://wiki:8000` → Sidebar-Link, `wiki_*`-Tools, Chat-/Agent-Spiegel nach `ki/` |
| Image | `ghcr.io/l8tenever/ida-wiki` (CI baut mit) |
| Port | `8083` → Cloudflare Tunnel `wiki.kulbarts.com` |

Die IDA-Seitenleiste öffnet **direkt** `WIKI_PUBLIC_URL` (kein Iframe). Cloudflare Zero Trust übernimmt die Anmeldung; die App selbst hat keine Login-Maske.
