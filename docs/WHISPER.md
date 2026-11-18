# IDA Whisper

Kleiner Dienst im Stack, der Sprachnachrichten aus IDA Chats **lokal** in Text umwandelt. Er läuft komplett auf dem Prozessor des Servers (keine Grafikkarte),
nichts geht nach außen – nur beim ersten Start lädt er das Modell von Hugging Face. Gedacht als Ersatz für die Umwandlung in Beeper, die Beeper bald abschaltet.

* **Modell:** [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) über [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  (CTranslate2, `int8` auf dem Prozessor), über `WHISPER_MODEL` austauschbar (z. B. `small` oder `medium`, wenn der Server zu langsam ist). Das Modell
  (~1,6 GB) lädt beim ersten Start ins Volume `whisper-data` (`/data/hf`) und bleibt dort. Gemessen auf dem Server: Laden und Anwärmen rund 170 s beim ersten Mal (mit Download) und rund 50 s mit dem
  Modell im Zwischenspeicher; danach belegt der Dienst etwa 2 GB Arbeitsspeicher (`WHISPER_MEM` begrenzt ihn, Standard 4g).
* **Ablauf:** `chats` holt die Audiodatei bei Beeper und schickt sie roh per `POST /api/v1/transkribieren` her. Ogg/Opus (WhatsApp, Signal), M4A, MP3, WebM u. ä. liest er selbst.
  Stille wird übersprungen (VAD), damit in Pausen keine Sätze erfunden werden. Es wird immer nur **eine** Datei gleichzeitig umgewandelt, weitere warten.
* **Sprache:** Standard ist die Erkennung pro Nachricht (`WHISPER_LANGUAGE` leer). Wer fast nur Deutsch bekommt, setzt `WHISPER_LANGUAGE=de`: das ist schneller und trifft kurze Nachrichten sicherer.
* **Tempo:** hängt am Prozessor und an dessen Auslastung. Gemessen mit 4 Threads auf dem Server (8 Kerne, dabei Last um 20): eine englische Aufnahme von 11 s brauchte 37–49 s, also das 3,4- bis 4,5-Fache
  der Länge (Wert aus drei Läufen hintereinander, auf einem sonst nicht leeren Server). Eine 30-s-Sprachnachricht dauert damit grob zwei Minuten. Die Nachrichten laufen nacheinander; bei vielen
  neuen Sprachnachrichten mit „Automatisch“ staut sich das. `WHISPER_THREADS` (Standard 4) und `WHISPER_BEAM` (1 = schnell, höher = genauer, aber langsamer) stellen es ein; ist es zu langsam,
  hilft ein kleineres Modell (`WHISPER_MODEL=medium` oder `small`) oder `WHISPER_LANGUAGE=de`.

## So nutzt es IDA Chats

Einstellungen → **Sprachnachrichten als Text**:

* **Umwandeln mit:** *Beeper* (Standard) oder *Lokal mit Whisper*. Wer *Lokal* wählt und keinen Whisper-Dienst hat (`WHISPER_URL` leer), bekommt eine Meldung. Ist der Dienst gerade nicht
  erreichbar oder das Modell lädt noch, zeigt IDA Chats eine Fehlermeldung und fällt **nicht** heimlich auf Beeper zurück.
* **Automatisch umwandeln:** Aus (Standard) = der Text kommt erst, wenn man am Text-Symbol der Sprachnachricht antippt. An = der Dienst wandelt **neue** Sprachnachrichten anderer von selbst um
  (auch bei geschlossener App, über den Archiv-Lauf) und zeigt den Text gleich unter der Nachricht. Nur Nachrichten, die nach dem Einschalten kamen, höchstens einen Tag alt, bis 10 Minuten Länge, und
  höchstens 20 gleichzeitig vorgemerkt; schlägt eine fehl, wird sie 10 Minuten lang nicht erneut versucht. Ältere oder längere wandelt man per Antippen um.

### Gesichert in der Datenbank

Jeder Text wird **einmal** umgewandelt und danach in `chats.db` (Tabelle `transcripts`, Volume `chats-data`) gespeichert – bleibt also bei Updates und Neustarts des Containers erhalten und steht auf allen Geräten
gleich da. Schlüssel ist die Adresse der Sprachnachricht ohne die Verschlüsselungsangaben nach dem „?“. Gespeichert werden Text, Engine (`beeper` oder `lokal`), Sprache und Zeit. Was Beeper selbst schon
mitliefert, sichert IDA Chats beim Laden der Nachrichten gleich mit – dieser Text wäre sonst mit Beepers Umwandlung weg. Gespeichert wird nur nach einer erfolgreichen Umwandlung (Fehler, „Modell lädt noch“ und
Zeitüberschreitung hinterlassen nichts). Ein leerer Text von Whisper („nichts erkannt“) zählt als Ergebnis; ein leerer Text von Beeper nicht (er heißt eher „noch nicht fertig“). Ein vorhandener Text
bleibt; nur ein leerer wird ersetzt. Die Datenbank sichert man mit dem Volume `chats-data` mit.

## API (nur im Stack, kein Port nach außen)

`POST /api/v1/transkribieren?language=` (Body = Audiodatei) → `{text, language, duration, took}` (413 zu groß, 422 unlesbar, 503 solange das Modell lädt) ·
`GET /api/v1/health` → `{ok, status (aus|lädt|bereit|fehler), error, model}`

In `chats`: `GET /api/v1/transcript?src=&mime=` → `{text}` (aus der Datenbank, sonst jetzt umgewandelt und gesichert; dauert es länger als 50 s, kommt `{text: "", pending: true}` – Cloudflare bricht Anfragen nach
~100 s ab –, die Umwandlung rechnet weiter, die Seite fragt alle 3 s erneut an, bis zu 10 Minuten, und hängt sich dabei an dieselbe Arbeit) · `GET /api/v1/stt/local` → `{configured, status, model, error, saved}` (Zustand des
Whisper-Dienstes und Zahl der gespeicherten Texte, für die Einstellungen) · Einstellungen `stt_local` und `stt_auto` über `GET`/`PUT /api/v1/prefs` (für alle Geräte gleich). Ist ein Text fertig, den die Automatik
umgewandelt hat, meldet der Dienst ihn über den Live-Kanal (`{"type": "stt", "id": <Chat>}`), offene Fenster laden den Chat neu.

## Einstellungen (Umgebung)

Im Dienst `whisper`: `WHISPER_MODEL` (Standard `large-v3-turbo`), `WHISPER_THREADS` (4), `WHISPER_COMPUTE` (`int8`), `WHISPER_BEAM` (1), `WHISPER_MAX_MB` (25), `WHISPER_LANGUAGE` (leer), `WHISPER_MEM` (Speichergrenze, 4g).
Im Dienst `chats`: `WHISPER_URL` (im Compose `http://whisper:8000`; leer = nur Beeper).

## Einrichten

`docker compose up -d chats whisper`. Der erste Start lädt das Modell (einige Minuten, Fortschritt in `docker compose logs -f whisper`); bis dahin zeigt Einstellungen → Sprachnachrichten
„lädt noch das Modell“. Danach in IDA Chats unter Einstellungen → Sprachnachrichten als Text → „Umwandeln mit“ auf *Lokal mit Whisper* stellen.
