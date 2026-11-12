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
* **Medien:** Bilder, Sprachnachrichten und Videos holt der Dienst über `get_attachment_image` (liefert die Datei als Binärteil) und reicht
  sie unter `/api/v1/media` mit Range-Unterstützung (Spulen, iPhone) weiter; bis 60 MB je Datei, im Speicher zwischengehalten (120 MB).
  Nur Bild-, Audio- und Videotypen werden angezeigt, alles andere (HTML, SVG …) nur als Download.
* **Sprachnachrichten:** eigener Player (Wellenform, Tempo 1×/1,5×/2×, Spulen). Den Text liefert Beeper oft schon mit; sonst wandelt ihn das
  lokale Whisper-Modell in Beeper um (`/api/v1/transcript`).
* **Bedienung:** Nach rechts wischen (oder Pfeil am PC) antwortet auf die Nachricht; langes Drücken am Handy bzw. Rechtsklick am PC öffnet das
  Menü (Antworten, Text kopieren, Datei öffnen); Tippen aufs Bild öffnet die Vollansicht.
* **Grenzen von Beeper-MCP** (nicht der App): Es gibt kein Werkzeug zum Reagieren (Emoji-Reaktionen), für Umfragen, zum Senden von Dateien oder
  Sprachnachrichten und keine Profilbilder (weder für Chats noch für Gruppenmitglieder; die App zeigt farbige Anfangsbuchstaben).
  Vorhandene Reaktionen werden angezeigt, soweit Beeper sie mitliefert. Das alles bräuchte Zugang zur Beeper-Desktop-API selbst.

## Einrichten

1. `.env`: `BEEPER_MCP_URL=https://beeper.kulbarts.com/v0/mcp`, `BEEPER_MCP_TOKEN=…`, `CHATS_PORT=10.7.0.1:8454`, optional `CHATS_PUBLIC_URL`. `docker compose up -d chats`.
2. Cloudflare: Access-App für `chats.kulbarts.com` (wie IDA), dann Tunnel-Route → `http://10.7.0.1:8454` (über das Dashboard).

## API (für die Oberfläche)

`GET /api/v1/chats?q=&unread=&inbox=&cursor=` · `GET /api/v1/chats/{id}/messages?cursor=&direction=` ·
`POST /api/v1/chats/{id}/send` `{text, reply_to}` · `GET /api/v1/image?src=` · `GET /api/v1/transcript?src=` · `GET /api/v1/stream` (SSE)
