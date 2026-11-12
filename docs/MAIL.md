# IDA Mail

Gmail im IDA-Stil als eigene App (Dienst `mail`, Image `ida-mail`, PWA mit eigenem Icon). Ordner, Suche, Konversationen lesen
(auch HTML-Mails), Anhänge, Schreiben, Antworten, Allen antworten, Weiterleiten (mit Anhängen), Entwürfe, Archivieren, Löschen,
Spam, Markieren, Gelesen/Ungelesen – und **live**: neue Mails und Änderungen erscheinen sofort.

```
Browser (mail.kulbarts.com, Cloudflare Access) ──> mail :8000 ──> Gmail REST-API
                                                      └── POST http://google:4571/api/v1/token/gmail  (kurzlebiger Token, nur Gmail-Scopes)
```

* **Kein eigener Speicher.** Alles liegt in Gmail. Die Google-Anmeldung liegt im Dienst `google` (IDA → Einstellungen → Google); der
  Mail-Dienst holt sich dort nur einen kurzlebigen Access-Token, eingeschränkt auf `gmail.modify` und `gmail.settings.basic` – nie den
  Refresh-Token. Ist Google nicht verbunden, zeigt die App einen Hinweis.
* **Live, aber sparsam:** Nur solange die App irgendwo offen (und sichtbar) ist, fragt der Dienst alle `LIVE_POLL_S` Sekunden (Standard 10)
  Gmails Änderungsliste ab (`history.list`, nur die Unterschiede) und schiebt sie per Server-Sent Events (`/api/v1/stream`) in die Seite.
  Ist keine App offen, fragt er gar nichts ab. Geht die App in den Hintergrund, trennt sie die Verbindung.
* **HTML-Mails** laufen in einem abgeschotteten Rahmen (`sandbox` ohne Skripte und Formulare, eigene Content-Security-Policy, Links öffnen
  in einem neuen Tab). Bilder von außen (oft Tracking-Pixel) sind blockiert, bis du auf „Bilder anzeigen“ tippst; eingebettete Bilder
  (`cid:`) kommen über `/api/v1/messages/{id}/attachments/{aid}`. Anhänge: Bilder/PDF/Text werden angezeigt, alles andere (auch HTML/SVG)
  nur heruntergeladen.
* **Schreiben:** Antworten bleiben in der Konversation (`threadId`, `In-Reply-To`, `References`). Absender wählbar, wenn in Gmail mehrere
  „Senden als“-Adressen eingerichtet sind. Beim Schließen eines angefangenen Entwurfs wird er in Gmail gespeichert.
* **Design:** `tokens.css`/`components.css`, Schriften und `dom.js` (Escaping, Links anklickbar) aus IDA; Akzentfarbe Orange (IDA-Palette
  „orange“). Icon: Briefumschlag mit Idas Augen (`frontend/mail/icons/`).

## Einrichten

1. Dienst `google` läuft und ist verbunden (siehe docs/GOOGLE.md). Der Mail-Dienst nutzt denselben `GOOGLE_API_TOKEN`.
2. `.env`: `MAIL_PORT=10.7.0.1:8453`, optional `MAIL_PUBLIC_URL=https://mail.kulbarts.com`. `docker compose up -d mail`.
3. Cloudflare: zuerst Access-App für `mail.kulbarts.com` (wie IDA), dann Tunnel-Route → `http://10.7.0.1:8453` (über das Dashboard,
   damit `warp-routing` erhalten bleibt).

## API (für die Oberfläche)

`GET /api/v1/me` · `GET /api/v1/labels` · `GET /api/v1/threads?label=&q=&page=` · `GET /api/v1/threads/{id}` ·
`POST /api/v1/threads/{id}/modify` `{add, remove}` · `POST /api/v1/threads/{id}/trash[?undo=true]` ·
`GET /api/v1/messages/{id}/attachments/{aid}` · `POST /api/v1/send` (Formular inkl. Dateien; `draft=true` speichert als Entwurf) ·
`DELETE /api/v1/drafts/{id}` · `GET /api/v1/stream` (SSE)
