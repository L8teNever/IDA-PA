# IDA Google

Ein Dienst im Stack für alles, was mit Google zu tun hat. Er ersetzt den einzeln laufenden Ida-Google-MCP
(Code stammt von github.com/L8teNever/Ida-Google, jetzt hier unter `google/`).

**Der Dienst hat keine öffentliche Adresse.** Alles läuft über IDA oder im Docker-Netz:

```
IDA (Einstellungen → Google) ──> Anmelden/Abmelden/Stand ─┐
andere Dienste im Stack ──────> :4571  REST-API (/api/v1/…)├─ Dienst "google" ──> Google
Agents (Veldora, Stella, …) ──> :4569  MCP (Bearer GOOGLE_MCP_TOKEN, nur im Haus) ┘      (Token im Volume google-data)
```

## Anmelden (Einstellungen → Google)

Der Knopf „Mit Google anmelden“ holt über die interne API die Adresse von Googles Zustimmungsseite (`POST /api/v1/auth/start`, mit
einmaligem `state`). Google fragt nach allen Berechtigungen und kommt zu **`<IDA_PUBLIC_URL>/api/v1/google/callback`** zurück. IDA reicht
`code` und `state` an `POST /api/v1/auth/callback` weiter, der Dienst prüft den `state`, tauscht den Code und speichert den Zugang. Danach
geht es zurück zu den Einstellungen, die zeigen das verbundene Konto und welche Dienste erlaubt sind. „Fehlendes erlauben“ wiederholt die
Anmeldung, „Abmelden“ widerruft den Zugang bei Google.

**Einmalig in der Google Cloud Console** (APIs & Dienste → Anmeldedaten → OAuth-Client → Autorisierte Weiterleitungs-URIs):
`https://ida.kulbarts.com/api/v1/google/callback` eintragen (die Adresse kommt aus `IDA_PUBLIC_URL`). Das geht nur dort, nicht per API.
Alte Weiterleitungs-URIs können gelöscht werden.

## MCP (nur intern)

Port 4569, Bearer `GOOGLE_MCP_TOKEN`. `GOOGLE_MCP_PORT` entscheidet, wo er lauscht: Standard `127.0.0.1:4569`; sollen Dienste in anderen
Stacks (z. B. Veldora, Stella) ihn erreichen, die interne Adresse des Servers eintragen (z. B. `10.7.0.1:4569`). Nie in den Tunnel hängen.

## Interne REST-API (für Dienste im Stack)

| Aufruf | Was |
|---|---|
| `GET /api/v1/status` | verbunden? Stand von Kalender-Abgleich und Docs-Backup |
| `GET /api/v1/auth/status` | Anmeldung: verbunden, Konto, welche Dienste erlaubt sind |
| `POST /api/v1/auth/start` / `auth/callback` / `auth/disconnect` | Anmelde-Ablauf (nutzt IDA) und Abmelden |
| `GET /api/v1/tools` | alle Google-Funktionen (dieselben wie im MCP) mit Beschreibung und Parametern |
| `POST /api/v1/tools/{name}` | eine Funktion ausführen, Body = JSON-Objekt mit den Parametern. Antwort `{"ergebnis": …}` |
| `GET /api/v1/sync/calendar` | Stand des letzten Kalender-Abgleichs |
| `POST /api/v1/sync/calendar?dry_run=1` | Abgleich jetzt (mit `dry_run`: nur anzeigen, was passieren würde) |
| `GET/POST /api/v1/backup/docs` | Stand / Docs-Sicherung jetzt nach Drive legen |
| `POST /api/v1/token/gmail` | kurzlebiger Access-Token nur mit Gmail-Scopes, für IDA Mail (docs/MAIL.md) |

Port 4571 wird nie veröffentlicht. Ist `GOOGLE_API_TOKEN` gesetzt, braucht jeder Aufruf `Authorization: Bearer <token>`.

Beispiele (aus einem anderen Container):

```
curl -s http://google:4571/api/v1/tools/google_termine_liste -X POST -H 'content-type: application/json' -d '{"max_ergebnisse": 5}'
```

Löschende Funktionen verlangen weiter `bestaetigt=true`, genau wie im MCP.

## Kalender-Abgleich (IDA-Kalender ⇄ Google-Kalender)

* Der Dienst `kalender` bleibt wie er ist (keine Verbindung nach außen). Der Abgleich läuft im Dienst `google` und spricht
  intern mit der Kalender-API (`http://kalender:8000`, als Agent `google-sync`, sichtbar im Verlauf).
* An mit `GOOGLE_CALENDAR_SYNC=1`. Alle 5 Minuten, Zeitraum 30 Tage zurück bis 365 Tage voraus. Danach sind **beide Seiten gleich**:
  neue, geänderte und gelöschte Termine gehen in beide Richtungen, egal wer sie angelegt hat (auch die Agents).
* **Alle Kalender:** Der Standardkalender von IDA gehört zum Hauptkalender von Google. Alle anderen Kalender werden über den Namen
  zugeordnet („Ida“ ⇄ „Ida“). Fehlt die Gegenseite, wird sie angelegt, aber nur, wenn der Kalender Termine hat. Feiertage, Geburtstage und
  abonnierte Kalender von Google bleiben außen vor. Nur einen Kalender abgleichen: `GOOGLE_CALENDAR_SYNC_KALENDER=<Name oder ID>`.
* Haben sich beide Seiten geändert, gewinnt die jüngere Änderung. Jede Seite wird nur mit ihrem eigenen letzten Stand verglichen,
  deshalb gibt es kein Hin-und-Her (Echo).
* Beim ersten Lauf werden gleiche Termine (Titel + Zeit) auf beiden Seiten verbunden, nicht doppelt angelegt.
* Abgeglichen werden Titel, Beschreibung, Ort, Zeit, ganztägig und Wiederholung (täglich, wöchentlich, monatlich, jährlich).
  Erinnerungen, Farben und Teilnehmer nicht. **Einzelne geänderte Vorkommen** einer Serie werden übersprungen und unter `skipped` gemeldet.
* Sicherheitsnetz: Würde ein Lauf mehr als 20 Termine in einem Kalender löschen, bricht er ab und löscht nichts (`CALENDAR_SYNC_MAX_DELETES`).
* Vorab ansehen, was passieren würde: `POST /api/v1/sync/calendar?dry_run=1` (ändert nichts).

## Docs-Backup nach Google Drive

* An mit `GOOGLE_DOCS_BACKUP=1`. Einmal täglich nach 03:00 Uhr holt der Dienst die ZIP-Sicherung aus IDA Docs
  (`GET http://docs:8000/api/v1/backup`) und legt sie im Drive-Ordner „IDA Docs Backup“ ab, die letzten 14 bleiben.
* Scope `drive.file`: der Dienst sieht in Drive nur, was er selbst angelegt hat.
* Sofort sichern: `POST /api/v1/backup/docs`.

## Einrichten

1. `.env` des Stacks: `COMPOSE_PROFILES=google` (mit Komma zu anderen Profilen), `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_MCP_TOKEN`,
   `GOOGLE_API_TOKEN`, `IDA_PUBLIC_URL`; ggf. `GOOGLE_MCP_PORT` (siehe oben). Dann `docker compose up -d google`.
2. Weiterleitungs-URI in der Google Cloud Console eintragen (siehe oben), dann in IDA unter Einstellungen → Google anmelden.
3. Nach neuen Google-Funktionen oder Scopes die Anmeldung einmal wiederholen (sonst „insufficient authentication scopes“).
