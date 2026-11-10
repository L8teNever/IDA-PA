# IDA Google

Ein Dienst im Stack für alles, was mit Google zu tun hat. Er ersetzt den einzeln laufenden Ida-Google-MCP
(Code stammt von github.com/L8teNever/Ida-Google, jetzt hier unter `google/`).

```
Agents / claude.ai ──Tunnel──> :4569  MCP (/mcp, Bearer GOOGLE_MCP_TOKEN)      ┐
Browser (einmalig) ──Access──> :4570  Anmeldung (/authorize, /oauth/callback)  ├─ Dienst "google"
andere Dienste im Stack ─────> :4571  REST-API (/api/v1/…) – nur Docker-Netz   ┘      │
                                                                                      ▼
                                              Google (OAuth-Refresh-Token in Volume google-data)
```

**Nur 4569 und 4570 gehören an den Host/Tunnel. 4571 wird nie veröffentlicht**, dort kommen die anderen Dienste ran:
`http://google:4571`. Ist `GOOGLE_API_TOKEN` gesetzt, braucht jeder Aufruf `Authorization: Bearer <token>`.

## Interne REST-API (für Dienste im Stack)

| Aufruf | Was |
|---|---|
| `GET /api/v1/status` | verbunden? Stand von Kalender-Abgleich und Docs-Backup |
| `GET /api/v1/tools` | alle Google-Funktionen (dieselben wie im MCP) mit Beschreibung und Parametern |
| `POST /api/v1/tools/{name}` | eine Funktion ausführen, Body = JSON-Objekt mit den Parametern. Antwort `{"ergebnis": …}` |
| `GET /api/v1/sync/calendar` | Stand des letzten Kalender-Abgleichs |
| `POST /api/v1/sync/calendar?dry_run=1` | Abgleich jetzt (mit `dry_run`: nur anzeigen, was passieren würde) |
| `GET/POST /api/v1/backup/docs` | Stand / Docs-Sicherung jetzt nach Drive legen |

Beispiele (aus einem anderen Container):

```
curl -s http://google:4571/api/v1/tools/google_termine_liste -X POST -H 'content-type: application/json' -d '{"max_ergebnisse": 5}'
curl -s http://google:4571/api/v1/tools/google_aufgaben_liste -X POST -d '{}'
```

Löschende Funktionen verlangen weiter `bestaetigt=true`, genau wie im MCP.

## Kalender-Abgleich (IDA-Kalender ⇄ Google-Kalender)

* Der Dienst `kalender` bleibt wie er ist (keine Verbindung nach außen). Der Abgleich läuft im Dienst `google` und spricht
  intern mit der Kalender-API (`http://kalender:8000`, als Agent `google-sync`, sichtbar im Verlauf).
* An mit `GOOGLE_CALENDAR_SYNC=1`. Standard: der Standardkalender von IDA ⇄ der Hauptkalender (`primary`) von Google;
  anderer IDA-Kalender mit `GOOGLE_CALENDAR_SYNC_KALENDER=<Name oder ID>`. Alle 5 Minuten, Zeitraum 30 Tage zurück bis 365 Tage voraus.
* Beide Richtungen: neue, geänderte und gelöschte Termine. Haben sich beide Seiten geändert, gewinnt die jüngere Änderung.
  Jede Seite wird nur mit ihrem eigenen letzten Stand verglichen, deshalb gibt es kein Hin-und-Her (Echo).
* Beim ersten Lauf werden gleiche Termine (Titel + Zeit) auf beiden Seiten verbunden, nicht doppelt angelegt.
* Serien (täglich, wöchentlich, monatlich, jährlich) werden als Serie abgeglichen. **Einzelne geänderte Vorkommen** einer Serie
  werden übersprungen und im Ergebnis unter `skipped` gemeldet.
* Sicherheitsnetz: Würde ein Lauf mehr als 20 Termine löschen, bricht er ab und löscht nichts (`CALENDAR_SYNC_MAX_DELETES`).
* Vorab ansehen, was passieren würde: `POST /api/v1/sync/calendar?dry_run=1` (ändert nichts).

## Docs-Backup nach Google Drive

* An mit `GOOGLE_DOCS_BACKUP=1`. Einmal täglich nach 03:00 Uhr holt der Dienst die ZIP-Sicherung aus IDA Docs
  (`GET http://docs:8000/api/v1/backup`) und legt sie im Drive-Ordner „IDA Docs Backup“ ab, die letzten 14 bleiben.
* Scope `drive.file`: der Dienst sieht in Drive nur, was er selbst angelegt hat.
* Sofort sichern: `POST /api/v1/backup/docs`.

## Einrichten / Umziehen

1. `.env` des Stacks: `COMPOSE_PROFILES=google` (mit Komma zu anderen Profilen), `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   `GOOGLE_REDIRECT_URI`, `GOOGLE_MCP_TOKEN`, `GOOGLE_AUTH_TOKEN`; `GOOGLE_MCP_PORT` / `GOOGLE_AUTH_PORT` (z. B. `10.7.0.1:4569`),
   wenn der Tunnel darauf zeigt. Dann `docker compose up -d google`.
2. Einmal mit Google verbinden: `https://<auth-adresse>/authorize?token=<GOOGLE_AUTH_TOKEN>`. Beim Umzug vom alten Container
   die Datei `google_token.json` ins Volume `google-data` kopieren, dann entfällt das.
3. Nach neuen Google-Funktionen oder Scopes die Anmeldung einmal wiederholen (sonst „insufficient authentication scopes“).
