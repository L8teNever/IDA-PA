# IDA Chats

Deine Beeper-Chats im IDA-Stil als eigene App (Dienst `chats`, Image `ida-chats`, PWA): Chats suchen (Alle / Ungelesen / Archiv),
Unterhaltungen lesen, Nachrichten schreiben, auf Nachrichten antworten, Bilder sehen, Sprachnachrichten als Text anzeigen – und **live**.

```
Browser (chats.kulbarts.com, Cloudflare Access) ──> chats :8000 ──MCP──> Beeper Desktop (beeper.kulbarts.com/v0/mcp)
```

* **Kein eigener Speicher.** Alles liegt in Beeper Desktop. Der Dienst spricht dessen MCP-Adresse an (`BEEPER_MCP_URL`) mit dem Bearer-Token `BEEPER_MCP_TOKEN` an – dem Zugang, den IDA beim Beeper-Anbieter speichert.
  dieselbe wie beim Beeper-Anbieter in IDA).
* **Live, aber sparsam:** Nur solange die App irgendwo offen (und sichtbar) ist, fragt der Dienst alle `LIVE_POLL_S` Sekunden (Standard 5)
  die 30 zuletzt aktiven Chats ab und meldet geänderte per Server-Sent Events (`/api/v1/stream`). Ohne offene App fragt er nichts ab.
* **Grenzen von Beeper-MCP** (nicht der App): Es gibt nur Text senden (mit Antwort auf eine Nachricht), Bilder holen und Sprachnachrichten
  in Text umwandeln. Darum gibt es **nicht**: Reaktionen setzen, Umfragen erstellen oder abstimmen, Sprachnachrichten abspielen, Videos
  abspielen, Dateien/Bilder senden. Vorhandene Reaktionen werden angezeigt, soweit Beeper sie mitliefert. Videos und Dateien erscheinen
  als Hinweis „in Beeper ansehen“. Dafür bräuchte es Zugang zur Beeper-Desktop-API selbst (Port 23373 mit eigenem Token).

## Einrichten

1. `.env`: `BEEPER_MCP_URL=https://beeper.kulbarts.com/v0/mcp`, `BEEPER_MCP_TOKEN=…`, `CHATS_PORT=10.7.0.1:8454`, optional `CHATS_PUBLIC_URL`. `docker compose up -d chats`.
2. Cloudflare: Access-App für `chats.kulbarts.com` (wie IDA), dann Tunnel-Route → `http://10.7.0.1:8454` (über das Dashboard).

## API (für die Oberfläche)

`GET /api/v1/chats?q=&unread=&inbox=&cursor=` · `GET /api/v1/chats/{id}/messages?cursor=&direction=` ·
`POST /api/v1/chats/{id}/send` `{text, reply_to}` · `GET /api/v1/image?src=` · `GET /api/v1/transcript?src=` · `GET /api/v1/stream` (SSE)
