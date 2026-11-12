# IDA Korrektur

Kleiner Dienst im Stack, der Tippfehler in Chats-Nachrichten korrigiert, bevor du sendest. Er läuft komplett lokal auf dem Prozessor,
nichts geht nach außen (nur beim ersten Start lädt er das Modell von Hugging Face).

* **Modell:** [oliverguhr/spelling-correction-german-base](https://huggingface.co/oliverguhr/spelling-correction-german-base) (T5, ~1 GB, Apache 2.0),
  über `KORREKTUR_MODEL` austauschbar. Liegt nach dem ersten Start im Volume `korrektur-data` (`/data/hf`).
* **Ablauf:** Der Text geht in Stücken von höchstens 25 Wörtern ans Modell (`KORREKTUR_CHUNK_WORDS`), Greedy-Suche mit knappem Längenlimit
  (sonst wiederholt das Modell ganze Sätze). Gleiche Texte kommen aus einem Zwischenspeicher. Immer nur eine Anfrage gleichzeitig.
* **Filter (`app/filter.py`):** Die Modell-Ausgabe wird Wort für Wort auf dein Original gelegt. Übernommen wird ein Wort nur, wenn es dem
  getippten ähnlich ist (Tippfehler-Abstand), auch Zusammenziehen/Trennen von bis zu 3 Wörtern. Vom Modell erfundene oder weggelassene Wörter,
  Wiederholungen, Links, Zahlen, @Namen, Abkürzungen (IDA) und Emojis bleiben wie getippt. Kommas vom Modell sind erlaubt, Punkte mitten im
  Text nicht; das Satzzeichen am Ende bleibt deins; Großschreibung übernimmt er nur am Satzanfang.
* **Tempo:** auf dem Server ohne Grafikkarte etwa 0,5–3 s pro Nachricht, je nach Länge und Last.
* **Qualität:** Das Modell ist ein Proof of Concept. Vieles trifft es („eknn“ → kann, „udj“ → und, „vesbbeserz wernde“ → verbessert werden),
  manches nicht („stakc“ → Stock). Deshalb wird nichts automatisch ersetzt, du wählst beim Senden.

## API (nur im Stack, kein Port nach außen)

`POST /api/v1/korrigieren` `{text}` → `{text, parts: [{t, neu}], changed}` (503, solange das Modell lädt) · `GET /api/v1/health` → `{ok, status}`

## Einstellungen

`KORREKTUR_THREADS` (Standard 4), `KORREKTUR_MEM` (Speichergrenze, Standard 3g), `KORREKTUR_MAX_CHARS` (1500), `KORREKTUR_MODEL`.
