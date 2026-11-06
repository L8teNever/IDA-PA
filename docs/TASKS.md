# IDA Tasks – Aufgaben-App

Eine eigene, schlanke Aufgaben-App im Stil von Kalender und IDA: ein eigener Dienst mit eigener Datenbank, einem eigenen Port und
einer eigenen PWA. Ida und Stella bedienen sie über die `tasks_*`-Tools. In der App kann man direkt mit Stella sprechen, die dort
in einem eigenen **Aufgaben-Modus** arbeitet.

## Bedienung

Es gibt drei Bereiche nebeneinander. Man wechselt oben über die Reiter oder wischt auf freier Fläche.

| Bereich | Inhalt |
|---|---|
| **Erledigt** (links) | Abgehakte Aufgaben. Wischen nach rechts holt sie zurück, nach links löscht sie. Nach 60 Tagen werden sie automatisch gelöscht. |
| **Heute** (Mitte, Start) | Alles, was heute dran ist, sortiert nach Priorität und Frist. |
| **Geplant** (rechts) | Aufgaben mit einem späteren Tag, nach Tagen gruppiert. Am geplanten Tag stehen sie von selbst wieder auf der Startseite. |

**Karten:**
- **Wischen nach rechts** öffnet „Wann machst du das?“: morgen, übermorgen, Wochenende, nächste Woche, in einer oder zwei Wochen oder ein Datum.
- **Wischen nach links** hakt die Aufgabe ab. Ein Toast bietet danach „Rückgängig“ an.
- **Antippen** öffnet die Details: Titel, Priorität (keine, niedrig, mittel, hoch), „Mach ich am“ (Tag), „Fertig bis“ (Frist) und Notiz.

**Neue Aufgaben:** Man tippt sie unten ein. Steht man im Bereich „Geplant“, wird sie automatisch für morgen angelegt.

**Sprechen:** Der pinke Knopf unten rechts startet Stella. Er erscheint nur, wenn `VOICE_SECRET` und `VOICE_PUBLIC_URL` gesetzt sind.

**Live-Updates:** Legen Ida oder Stella etwas an, erscheint es sofort. Die Verbindung dafür (SSE) ist nur offen, solange die App sichtbar ist.

## Datenmodell

Es gibt nur eine Tabelle `tasks` in `tasks.db` (SQLite) im Volume `tasks-data`:

| Feld | Bedeutung |
|---|---|
| `day` | Ab diesem Tag steht die Aufgabe auf der Startseite. Leer heißt sofort, ein späterer Tag heißt „Geplant“. |
| `due` | Bis wann sie fertig sein muss (Frist oder Ende eines Zeitraums). Das ist nur Anzeige und Sortierung, ist sie vorbei, steht „überfällig“ dran. |
| `priority` | 0 = keine, 1 = niedrig, 2 = mittel, 3 = hoch |
| `done_at` | Zeitpunkt, an dem sie erledigt wurde |
| `created_by`, `updated_by` | `user` oder die Agent-ID. Die App zeigt dann z. B. „von Stella“. |

Die API (`/api/v1/tasks`, `/counts`, `/stream`, `/voice/ticket`) versteht für Tage außer ISO-Daten auch Wörter wie „morgen“,
„freitag“, „nächste woche“, „in 3 tagen“ oder „12.11.“ (`tasks/app/days.py`). So müssen Ida und Stella nicht selbst rechnen.

**Wer ruft an?**
- **Ohne Token:** Das gilt als du selbst, also die Oberfläche. Deshalb gehört **Cloudflare Access** davor.
- **Mit `Bearer TASKS_IDA_TOKEN`:** Das ist IDA. `X-Tasks-By` sagt, welcher Agent es war.

## Stella im Aufgaben-Modus

1. Die App holt sich bei ihrem eigenen Server ein Ticket (`POST /api/v1/voice/ticket`). Es hat dasselbe Format wie die Tickets von
   IDA und ist mit `VOICE_SECRET` signiert, eine Minute gültig und nur einmal nutzbar.
2. Dann verbindet sie sich direkt mit dem Voice-Dienst: `wss://<VOICE_PUBLIC_URL>/live?ticket=…&mode=tasks`.
3. `voice_bridge.start(mode="tasks")` gibt Stella dann:
   - **einen eigenen Prompt** (`TASKS_PERSONA` und `TASKS_RULES`) statt ihres normalen Profils. Gedächtnis und Wiki-Kern
     bekommt sie weiterhin mit.
   - **nur diese Tools:** `tasks_*`, den Kalender **nur lesend** (`calendar_events`, `calendar_search`, `calendar_day_brief` …),
     `memory_search`, `memory_save`, `wiki_search`, `wiki_read` und `wissen_suchen`.
   - **keine** Connectoren, kein `tool_suchen` und kein `ida_fragen`.
4. Anlegen, Ändern und Abhaken laufen ohne Nachfrage, denn dass Simon es sagt, ist schon sein Ja. Löschen braucht weiter
   `bestaetigt=true`.
5. Hängt eine Aufgabe an einem Termin („wenn ich beim Zahnarzt bin“), schaut Stella zuerst im Kalender nach und plant sie passend.
6. Die Mitschrift landet als Chat „Aufgaben-Gespräch · …“ bei Stella, danach folgt die übliche Auswertung.

## Einrichten

1. **`.env`:** `TASKS_IDA_TOKEN` setzen (`openssl rand -hex 32`), optional `TASKS_PORT`, z. B. `10.7.0.1:8449`.
   Für das Sprechen nutzt der Dienst `VOICE_SECRET` und `VOICE_PUBLIC_URL` aus derselben `.env`.
2. **Starten:** `docker compose pull tasks app voice && docker compose up -d tasks app voice`.
3. **Cloudflare:** Im Tunnel z. B. `tasks.kulbarts.com` auf den Port leiten, **mit Access davor** (nur du).
4. **Testen:** App öffnen, eine Aufgabe tippen, wischen und auf den pinken Knopf tippen.

**Code:**
- Dienst: `tasks/`
- Oberfläche: `frontend/tasks/`
- IDA-Tools: `backend/app/services/mcp/tasks_tools.py`
- Stella-Modus: `backend/app/services/voice_bridge.py` (`TASKS_*`)
- Tests: `tasks/tests`, `backend/tests/test_tasks.py`
