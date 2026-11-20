# IDA Piper

Kleiner Dienst im Stack, der Stellas Antworten in der **eigenen Kette** (Einstellungen → Stella) lokal in Sprache umwandelt –
mit [Piper](https://github.com/OHF-Voice/piper1-gpl) auf dem Prozessor, ohne Grafikkarte. Nichts geht nach außen, außer beim ersten
Gebrauch einer Stimme: die lädt er von Hugging Face ([rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)) ins Volume `piper-data`.

* **Stimmen:** alle aus `voices.json` von piper-voices (Liste eine Woche zwischengespeichert). Standard `de_DE-thorsten-high` (~114 MB,
  lädt beim Start vor). Deutsch: thorsten (low/medium/high), thorsten_emotional, kerstin, ramona, eva_k, karlsson, pavoque, mls.
* **Tempo** (gemessen auf dem Server bei hoher Last): `thorsten-medium` rechnet etwa 0,35 × Echtzeit, `thorsten-high` etwa 1,4 ×.
  Stella schickt Satz für Satz, der erste Satz ist also schnell da; ist `high` zu träge, `medium` wählen.
* **Speicher:** höchstens `PIPER_KEEP` (2) Stimmen gleichzeitig geladen. Es rechnet immer nur ein Satz gleichzeitig.

## API (nur im Stack, kein Port nach außen)

`POST /api/v1/tts` `{text, voice?, speed?}` → WAV (16 bit mono, Rate je nach Stimme, auch im Header `x-sample-rate`; 400 leer, 404 unbekannte
Stimme, 413 zu lang) · `GET /api/v1/voices?lang=de` → `{voices: [{key, name, language, quality, speakers, mb, downloaded}], default}` ·
`GET /api/v1/health`.

In IDA: `GET /api/v1/voice/piper/voices` und `GET /api/v1/voice/piper/sample/{stimme}?speed=` (Hörprobe für die Einstellungen).

## Einstellungen (Umgebung)

Im Dienst `piper`: `PIPER_DEFAULT_VOICE` (`de_DE-thorsten-high`), `PIPER_PRELOAD` (true), `PIPER_KEEP` (2), `PIPER_MAX_CHARS` (2000),
`PIPER_DIR` (`/data/voices`), `PIPER_MEM` (Speichergrenze im Compose, 1500m). In `app` und `voice`: `PIPER_URL` (im Compose `http://piper:8000`).

## Einrichten

`docker compose up -d piper app voice`, dann Einstellungen → Stella → *Eigene Kette*. Dazu in der `.env` den Cloudflare-Zugang
(`CLOUDFLARE_AI_ACCOUNT_ID`, `CLOUDFLARE_AI_TOKEN` mit dem Recht „Workers AI“), siehe [STELLA.md](STELLA.md).
