# IDA Chats

Deine Beeper-Chats im IDA-Stil als eigene App (Dienst `chats`, Image `ida-chats`, PWA): Chats suchen (Filter-Knopf rechts in der Suche: Alle / Ungelesen / Archiv),
Unterhaltungen lesen, Nachrichten schreiben, auf Nachrichten antworten, Bilder sehen, Sprachnachrichten als Text anzeigen – und **live**.

```
Browser (chats.kulbarts.com, Cloudflare Access) ──> chats :8000 ──MCP──> Beeper Desktop (beeper.kulbarts.com/v0/mcp)
```

* **Fast kein eigener Speicher.** Chats und Nachrichten liegen in Beeper Desktop; der Dienst merkt sich nur gesehene Nachrichten (siehe „Löschen und gelöschte Nachrichten“). Der Dienst spricht dessen MCP-Adresse an (`BEEPER_MCP_URL`) mit dem Bearer-Token `BEEPER_MCP_TOKEN` an – dem Zugang, den IDA beim Beeper-Anbieter speichert.
  dieselbe wie beim Beeper-Anbieter in IDA).
* **Live, aber sparsam:** Nur solange die App irgendwo offen (und sichtbar) ist, fragt der Dienst alle `LIVE_POLL_S` Sekunden (Standard 5)
  die 30 zuletzt aktiven Chats ab und meldet geänderte per Server-Sent Events (`/api/v1/stream`). Ohne offene App fragt er nichts ab.
* **Medien:** Bilder, Sprachnachrichten und Videos holt der Dienst über `get_attachment_image` (liefert die Datei als Binärteil) und reicht
  sie unter `/api/v1/media` mit Range-Unterstützung (Spulen, iPhone) weiter; bis 60 MB je Datei, im Speicher zwischengehalten (120 MB).
  Nur Bild-, Audio- und Videotypen werden angezeigt, alles andere (HTML, SVG …) nur als Download.
* **Sprachnachrichten:** eigener Player (Wellenform, Tempo 1×/1,5×/2×, Spulen). Den Text liefert Beeper oft schon mit; sonst wandelt ihn das
  lokale Whisper-Modell in Beeper um (`/api/v1/transcript`).
* **Bedienung:** Fremde Nachrichten nach rechts, eigene nach links wischen (oder Pfeil am PC) antwortet darauf; langes Drücken am Handy bzw. Rechtsklick am PC öffnet das
  Menü (Antworten, Text kopieren, Datei öffnen); Tippen aufs Bild öffnet die Vollansicht.
* **Adressen für Bild und Video:** Ein geöffnetes Bild bekommt `/chat/<id>/foto/<Nachricht>` (bei mehreren Bildern in einer Nachricht `/<Nr>`),
  ein Video `/chat/<id>/video/<Nachricht>`, ein Profilbild `…/bild`. Beim Durchwischen ändert sich die Adresse mit. Zurück (auch die Taste am Handy)
  schließt das Vollbild; Neuladen oder ein Link öffnen es wieder, sofern die Nachricht im geladenen Verlauf liegt, sonst bleibt nur der Chat offen.
  Die Info-Seite (Tipp auf den Chat-Kopf) hat `/chat/<id>/info`; ein Bild von dort öffnet darüber, Zurück führt Schritt für Schritt zurück.
* **Vollbild-Bild:** unten dieselbe Leiste wie im Video-Player (Reagieren, Antworten, Weiterleiten, Herunterladen). Neben Bildern und Videos im Verlauf
  gibt es keinen Download-Knopf mehr; heruntergeladen wird nur im Vollbild bzw. Video-Player.
* **Scrollen beim Öffnen:** Der Verlauf springt ans Ende und bleibt dort, solange Bilder, Video-Vorschauen oder Schrift nachladen
  (ResizeObserver auf die Nachrichten, `overflow-anchor: none`). Wer selbst hochscrollt, bleibt an seiner Stelle.
* **Profilbilder:** kommen aus der REST-API von Beeper Desktop (gleiche Adresse ohne `/v0/mcp`, gleicher Token; abweichend über
  `BEEPER_API_URL`): `GET /v1/chats/{id}` liefert `imgURL` von Chat und Teilnehmern (als `file:///…` oder Windows-Pfad), die Bilder holt
  `GET /v1/assets/serve?url=…`. Der Dienst gibt sie unter `/api/v1/avatar/{chat}` (Gruppenbild bzw. andere Person) und
  `/api/v1/avatar/{chat}?user={id}` (Gruppenmitglied) aus, zwischengespeichert; ohne Bild (404) bleibt der farbige Anfangsbuchstabe.
  In Gruppen steht das Bild an der letzten Nachricht einer Folge vom selben Absender. Der Name steht frei über der ersten Blase der Folge (nicht in der Blase), jede Person in eigener Farbe.
* **Lesebestätigung:** Beeper liefert pro Nachricht `seen` ({Nutzer: Zeitpunkt}); die App zeigt bei eigenen Nachrichten ein Häkchen
  (gesendet) oder zwei mintfarbene (gelesen). Wer wann gelesen hat, zeigt langes Drücken → „Info“ (nicht mehr im Verlauf). Der offene Chat wird alle 4 s neu geholt.
* **Senden von Dateien und Sprachnachrichten:** „+“ öffnet Fotos & Videos, Kamera, Datei (auch Einfügen und Ziehen am PC); vor dem Senden
  eine Vorschau mit Bildunterschrift, bis 10 Dateien, je höchstens 95 MB (Cloudflare-Grenze). Bei leerem Feld wird aus Senden ein Mikrofon:
  Antippen nimmt auf (Wellenform, Zeit, Pause, Verwerfen, höchstens 15 Minuten). `POST /api/v1/chats/{id}/upload` lädt die Datei per
  `POST /v1/assets/upload` zu Beeper und schickt sie mit `POST /v1/chats/{id}/messages` (`attachment.uploadID`, Typ `voice-note` für
  Sprachnachrichten). Aufnahmen wandelt ffmpeg im Image vorher in Ogg/Opus (mono, 32 kbit/s) – das Format, das WhatsApp & Co. als Sprachnachricht erwarten.
* **Reaktionen:** langes Drücken → Reaktionsleiste; `POST`/`DELETE /api/v1/chats/{id}/messages/{msg}/react` → Beeper `…/reactions`.
* **Bearbeiten:** langes Drücken auf eine eigene Textnachricht → „Bearbeiten“; `PUT /api/v1/chats/{id}/messages/{msg}` → Beeper `PUT …/messages/{msg}`.
  Nur Text, keine Nachrichten mit Anhang; ob und wie lange es geht, entscheidet das Netzwerk (sonst kommt die Fehlermeldung von Beeper).
* **Weiterleiten:** langes Drücken → „Weiterleiten“ → Chat aussuchen; `POST /api/v1/chats/{ziel}/forward` mit Text und Anhängen (`src`).
  Die Beeper-API hat kein echtes Weiterleiten: Der Dienst holt die Anhänge bei Beeper und sendet alles im Zielchat neu –
  beim Empfänger steht deshalb kein „Weitergeleitet“.
* **Grenzen von Beeper-MCP** (nicht der App): Kein Werkzeug für Reaktionen, Dateien und Gelesen-Markieren –
  dafür nutzt die App die REST-API. „Als gelesen markieren“ geht über `/v1/chats/{id}/read` (auch in Beeper gelesen), „Als ungelesen markieren“
  (Knopf oben im Chat, „Ab hier ungelesen“ im Menü einer Nachricht) über `/v1/chats/{id}/unread`. Öffnen und Lesen allein markiert nichts. Umfragen sind noch nicht eingebaut.
* **Schnell laden:** Nachrichten kommen über die REST-API (`GET /v1/chats/{id}/messages`, ~100 ms), nur wenn die nicht geht über MCP.
  Ältere Seiten hält der Dienst 10 Minuten im Speicher; die Seite holt die nächste ältere Seite schon im Hintergrund („Ältere laden“ ist sofort da)
  und merkt sich pro Chat die letzten 150 Nachrichten (40 Chats, `localStorage`), so steht ein Chat beim Öffnen sofort da und wird dann aufgefrischt.
  Geladene Profilbilder werden beim Neuzeichnen nicht neu eingeblendet.
* **Anpinnen:** Langes Drücken (am PC Rechtsklick) auf einen Chat in der Liste → „Anpinnen“, höchstens 3. Das sind echte Beeper-Pins
  (`PATCH /v1/chats/{id}` mit `isPinned`), sie stehen also auch in Beeper oben. Welche angepinnt sind, liest `GET /api/v1/pins` aus `GET /v1/chats` (60 s gemerkt).
* **Zusammenführen:** Langes Drücken auf einen Chat → „Mit anderem Chat zusammenführen“. Das passiert nur in IDA Chats (Tabelle `merges` in
  `chats.db`), Beeper bleibt unberührt. Die Gruppe heißt nach dem ersten Chat, ihre ID ist dessen ID. `GET /api/v1/chats/{gruppe}/messages` liefert
  alle Chats als einen Verlauf; jede Nachricht hat `chat` und `mid` (echte ID), `id` ist `chat~mid`, der Cursor merkt sich pro Chat den Stand.
  Gesendet wird über den Chip im Eingabefeld (Standard: Chat der letzten eingehenden Nachricht), Antworten gehen in den Chat der zitierten Nachricht – der Chip springt dabei auf dessen Netzwerk.
  `POST /api/v1/merges`, `DELETE /api/v1/merges/{gruppe}[?chat=…]` (ganze Gruppe bzw. einen Chat herausnehmen).
* **Farben:** Eigene Nachrichten in der Farbe des Netzwerks; eingehende behalten die Blase, nur Akzente (Name, Zitat, Links, Sprachnachricht,
  Antwortleiste) bekommen die Netzwerkfarbe. Mehrere Konten pro Netzwerk (z. B. ein zweites, lokal verbundenes Signal) erkennt die App am Kontonamen.
* **Tippfehler-Vorschlag:** 1 s nach dem letzten Tippen fragt die App den Dienst `korrektur` (docs/KORREKTUR.md). Hat er etwas korrigiert, steht der
  Vorschlag über dem Eingabefeld, geänderte Wörter markiert, mit eigenem Senden-Knopf. Der normale Knopf schickt dein Original, Antippen des
  Vorschlags übernimmt ihn ins Feld. `POST /api/v1/korrektur` `{text}` → `{changed, text, parts}`; `KORREKTUR_URL` leer = aus.

## Einrichten

1. `.env`: `BEEPER_MCP_URL=https://beeper.kulbarts.com/v0/mcp`, `BEEPER_MCP_TOKEN=…`, `CHATS_PORT=10.7.0.1:8454`, optional `CHATS_PUBLIC_URL`. `docker compose up -d chats korrektur`.
2. Cloudflare: Access-App für `chats.kulbarts.com` (wie IDA), dann Tunnel-Route → `http://10.7.0.1:8454` (über das Dashboard).

## API (für die Oberfläche)

`GET /api/v1/chats?q=&unread=&inbox=&cursor=` · `GET /api/v1/chats/{id}/messages?cursor=&direction=` ·
`POST /api/v1/chats/{id}/send` `{text, reply_to}` · `GET /api/v1/image?src=` · `GET /api/v1/transcript?src=` · `GET /api/v1/stream` (SSE)

## Löschen und gelöschte Nachrichten

- **Löschen:** Lange auf eine Nachricht drücken. Eigene: „Für alle löschen“ (auch im Netzwerk, z. B. WhatsApp, wenn es das kann) oder
  „Nur für mich löschen“. Fremde: „Für mich löschen“. Läuft über `DELETE /v1/chats/{id}/messages/{id}?forEveryone=…` der Beeper-REST-API.
  Für mich Gelöschtes blendet der Dienst zusätzlich selbst aus.
- **Gelöschtes bleibt lesbar:** Der Dienst merkt sich jede Nachricht, die er einmal gesehen hat (`chats.db` im Volume `chats-data`,
  `DATA_DIR=/data`). Meldet Beeper eine Nachricht später als gelöscht, steht in der Blase „Diese Nachricht wurde gelöscht“ und darunter
  der frühere Inhalt. Anhänge sind nur noch da, solange Beeper die Datei noch liefert.
- **Merken im Hintergrund:** Auch bei geschlossener App fragt der Dienst alle `ARCHIVE_POLL_S` Sekunden (Standard 60, 0 = aus) die
  30 zuletzt aktiven Chats ab und lädt geänderte einmal. Was innerhalb dieser Zeit geschickt und wieder gelöscht wird, fehlt.

## Benachrichtigungen und Stummschalten

- **Einschalten:** Glocke oben in der Liste antippen und erlauben. Das meldet dieses Gerät per Web Push an (`chats/app/push.py`,
  Bibliothek `pywebpush`). Die Schlüssel (VAPID) erzeugt der Dienst beim ersten Gebrauch selbst, Schlüssel und Abos liegen in `chats.db`.
- **Akku:** Die App läuft dafür nicht, es gibt keinen Timer und keine offene Verbindung. Der Dienst fragt Beeper alle `PUSH_POLL_S`
  Sekunden (Standard 10, 0 = keine Benachrichtigungen), solange mindestens ein Gerät angemeldet ist. Steigt bei einem Chat die
  Ungelesen-Zahl, geht eine Meldung über den Push-Dienst des Browsers raus (Android/Chrome: Googles FCM, Inhalt Ende-zu-Ende
  verschlüsselt, `Urgency: high`). Das Handy wird nur dann geweckt.
- **Meldung:** Titel ist der Chat bzw. der eigene Name einer Zusammenführung, darunter der Text (in Gruppen „Name: Text“, ohne Text
  „📷 Foto“, „🎤 Sprachnachricht“ …). Als Bild dient das Profilbild der Person bzw. des Chats, im Service Worker rund zugeschnitten.
  Eine Meldung pro Chat (`tag`), neue ersetzen die alte. Tippen öffnet den Chat. Ist die App gerade offen und im Vordergrund,
  kommt keine Meldung, weil dort schon der Hinweis oben erscheint. Öffnest du einen Chat, verschwinden seine Meldungen.
- **Stummschalten:** Chat-Kopf antippen → Info → „Stummschalten“ (oder in der Liste lange drücken): 1 Stunde, 8 Stunden oder bis du
  es aufhebst. Das gilt nur für Benachrichtigungen dieser App, Beeper bleibt unberührt. In Beeper stummgeschaltete Chats bleiben
  ebenfalls still. In der Liste zeigt ein durchgestrichenes Glocken-Symbol stumme Chats.
- API: `GET /push` (öffentlicher Schlüssel), `POST /push/subscribe`, `POST /push/unsubscribe`, `GET /mutes`,
  `PUT /chats/{id}/mute` (`{"hours": 1 | 8 | 0}`, 0 = für immer), `DELETE /chats/{id}/mute`.
