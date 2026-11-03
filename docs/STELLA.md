# Stella – Live-Sprache in IDA

Stella ist der Agent für Gespräche. Gesprochen wird mit **Gemini Live**, und zwar an zwei Stellen:

- **Live-Knopf in der IDA-App** (im Eingabefeld, wo früher das Mikrofon war). Du redest direkt mit Stella, ohne Anruf.
- **Telefon über Telnyx:** Stella ruft an (`stella_call`), oder du rufst sie an.

Nur das Gespräch selbst läuft über Gemini (`GEMINI_API_KEY`). Alles Geschriebene läuft über das Modell, das beim Agenten
Stella ausgewählt ist: der Text-Chat mit Stella und die Auswertung nach dem Gespräch (Zusammenfassung, Gedächtnis, Aufgaben an Ida).

## Aufbau

```
Browser ──(Ticket von IDA)──► voice:/live ─┐
Telnyx ──► voice:/telnyx/webhook + /media ──┼─► Gemini Live (WebSocket)
                                            │
                     voice ──intern──► app:/api/v1/voice/internal/*  (Kontext, Tools, Ergebnis)
```

- **Container `voice`:** gleiches Image wie IDA, eigener Prozess (`uvicorn app.voice.main:app`). Code: `backend/app/voice/`.
  Er hat keine eigenen Daten. Updates der App unterbrechen deshalb kein Gespräch, und Watchtower aktualisiert ihn nur,
  wenn gerade niemand spricht (Hook `python -m app.voice.idle`, Exit 75 = später).
- **IDA-Seite:** `backend/app/services/voice_bridge.py` und `backend/app/api/voice.py`. Die Gespräche stehen in der Tabelle
  `voice_calls`, die Mitschrift wird ein Chat des Agenten Stella.
- **Ida löst Anrufe aus** mit den internen Tools `stella_call`, `stella_call_status`, `stella_busy` und `stella_recent_calls`
  (Datei `services/mcp/voice_tools.py`). Diese Tools sehen nur Ida und Stella.

## Zwei Modi: entschieden nur über die Nummer

| Wer ist dran? | Kontext | Tools |
|---|---|---|
| **Du**: Live-Knopf in der App, Stella ruft `STELLA_OWNER_NUMBER` an, oder du rufst von dieser Nummer an | Profil von Stella, Gedächtnis, Wiki-Kern, Uhrzeit | ja (siehe unten) |
| **Jemand anderes** (nur, wenn Ida eine andere Nummer anrufen lässt) | nur Profil und Auftrag | **keine** |

- **Eingehende Anrufe von fremden Nummern** nimmt Stella gar nicht an. Sie werden als `rejected` vermerkt.
- **Nach Gesprächen mit Fremden** läuft keine Auswertung mit Tools. Es gibt nur die Mitschrift und einen Hinweis an dich.
  Was ein Fremder sagt, kann also keinen Agenten zu einer Aktion bringen.
- **Die Anrufernummer lässt sich fälschen** (Caller-ID-Spoofing). Das ist bewusst in Kauf genommen. Abgefedert wird es
  dadurch, dass Schreib-Aktionen dein Ja brauchen und SSH und Cloudflare gesperrt sind.

## Tools im Gespräch

- **Was Stella nutzen darf:** die internen Tools von IDA mit den Rechten eines normalen Laufs von Stella (Gedächtnis,
  Wiki, Kalender, Erinnerungen, Tracker, `agent_delegate` an Ida usw.). Dazu alle MCP-Connectoren, die **Ida oder Stella**
  freigegeben sind. Stella kann also, was Ida kann.
- **Direkt angeboten** wird nur ein Kernsatz (`VOICE_CORE_TOOLS`). Alle anderen erreicht Gemini über `tool_suchen` und
  `tool_ausfuehren`, denn mehr als 200 Tools auf einmal machen Gemini Live langsam und ungenau.
- **Lesen geht sofort.** Für Schreib-Aktionen (alles, was laut Name nicht nur liest) gilt `bestaetigt=true`. Ohne diesen
  Wert führt IDA nichts aus und sagt Gemini, dass es erst nachfragen soll. Ausgenommen sind `memory_save` („merk dir …“)
  und `tracker_check`.
- **Nie erlaubt** sind Tools aus `VOICE_DENY_TOOLS` (Standard: SSH, Cloudflare, Code, `stella_*`, Agenten und Skills
  verwalten, Löschen von Routinen, Trackern und Gedächtnis).

## Einrichten

1. **`.env` ausfüllen:** `VOICE_SECRET` (`openssl rand -hex 32`), `VOICE_PUBLIC_URL`, `GEMINI_API_KEY`, `STELLA_OWNER_NUMBER`.
   Fürs Telefon zusätzlich `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID`, `TELNYX_FROM_NUMBER` und `TELNYX_PUBLIC_KEY`.
2. **Cloudflare-Tunnel:** `voice.example.com` auf `http://<server>:8086` leiten. Davor darf **kein Cloudflare Access**
   stehen, weil Telnyx und der Browser-WebSocket hin müssen. Geschützt ist der Dienst trotzdem:
   - Webhooks nur mit gültiger Ed25519-Signatur,
   - Media-Streams nur mit einem Schlüssel pro Anruf,
   - `/live` nur mit einem Ticket von IDA (eine Minute gültig, nur einmal nutzbar),
   - `/internal/*` nur mit `VOICE_SECRET`.
3. **Telnyx (Mission Control):** Die Webhook-URL der Call-Control-App auf `https://voice.example.com/telnyx/webhook` stellen.
   Der alte Pfad `/webhooks/telnyx` geht auch. Wer vom alten Stella umzieht, lässt die URL also, wie sie ist, und legt
   `VOICE_PORT` auf den alten Port, zum Beispiel `10.7.0.1:7878`.
4. **Starten:** `docker compose up -d`. In der App erscheint der Live-Knopf, sobald `VOICE_SECRET` und `VOICE_PUBLIC_URL`
   gesetzt sind.
5. **Altes Stella abschalten:** den alten Container stoppen und die `stella_*`-Tools im Connector `ida_1` ausschalten,
   damit Ida die neuen internen Tools nimmt. Danach das alte Stella-Repo archivieren.

## Feineinstellungen (`.env` & App)

- **Stimme auswählen:** Direkt in der App unter **Einstellungen → Stella-Stimme** (oder bei Agent Stella). Dort können alle 21 Gemini-Stimmen mit Audiobeispielen probegehört und ausgewählt werden. Änderungen greifen sofort ohne Neustart.
- Die Stimme wird nur in den Einstellungen (Stella-Stimme) gewählt, nicht in der `.env`; ohne Auswahl gilt `Aoede`.
- `GEMINI_LIVE_MODEL` und `GEMINI_LIVE_FALLBACKS`: Die Fallbacks werden nur beim Verbindungsaufbau versucht.
- `VOICE_PHONE_VAD`, `VOICE_VAD_RMS`, `VOICE_VAD_SILENCE_MS`, `VOICE_VAD_MIN_SPEECH_MS`: die eigene Sprach-Erkennung am
  Telefon gegen Auto- und Motorgeräusche (aus dem alten Stella).
- `VOICE_GREETING_BARGE_IN_S`: Wie lange Geräusche auf der Leitung die erste Begrüßung nicht unterbrechen dürfen.
- `VOICE_ECHO_RMS_FACTOR`: Echo-Sperre, solange Stella noch zu hören ist.
- `VOICE_MAX_CALL_MIN`: Nach so vielen Minuten legt Stella spätestens auf.
