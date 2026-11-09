# IDA Docs – Dokumente schreiben

Eigene App (Dienst `docs`, Ordner `dokumente/` + `frontend/docs/`) für Aufsätze, Referate und Projektarbeiten: Markdown schreiben, daneben die Seitenvorschau in DIN A4.

- **Speicherung auf dem Server:** Jedes Dokument liegt im Volume `docs-data` zweimal: `docs/<id>.idadocs` (JSON mit Titel, Text, Einstellungen) und `docs/<id>.md` (nur der Text). Beide werden bei jedem Speichern zusammen geschrieben. Der Browser sichert zusätzlich lokal mit, falls die Verbindung weg ist, und lädt später hoch.
- **`.idadocs`:** Einstellungen (Ränder, Schrift, Abstände …) stehen nur drin, wenn sie vom Standard abweichen. Import und Export gehen mit `.idadocs` und `.md`.
- **Standard:** DIN A4, Ränder links 30 / rechts 20 / oben 25 / unten 20 mm, Arial 11 pt, Zeilenabstand 1,5, Blocksatz, H1/H2/H3 = 16/14/12 pt, nummerierte Überschriften, Seitenzahl unten Mitte. Alles pro Dokument einstellbar (Zahnrad-Knopf). Die Werte stehen in `DEFAULTS` in `dokumente/app/store.py` **und** `frontend/docs/js/app.js` und müssen gleich bleiben.
- **Ansichten:** „Seiten“ = echte A4-Blätter mit Seitenumbruch (Spalten-Layout: jede Spalte ist eine Seite), „Web“ = fortlaufend wie eine Webseite. Hell/Dunkel gilt für das Papier. PDF: „Als PDF speichern“ öffnet den Druckdialog (Ziel „Als PDF speichern“, Ränder „Keine“).
- **Markdown:** Überschriften, Listen (verschachtelt), fett/kursiv/durchgestrichen, Code, Zitate, Tabellen (mit `:--:`), Links, Bilder per Adresse, Fußnoten `[^1]`. Erweiterungen: `[TOC]` (Inhaltsverzeichnis mit Seitenzahlen), `\newpage`, `# Titel {-}` (ohne Nummer), `::: center|right|left|justify|title` … `:::`. Bausteine in der Werkzeugleiste: Titelblatt, Quellenverzeichnis, Selbstständigkeitserklärung.

## Backup nach Google Drive (optional)

Mit `DOCS_GOOGLE_CLIENT_ID`, `DOCS_GOOGLE_CLIENT_SECRET` und `DOCS_GOOGLE_REFRESH_TOKEN` (OAuth, Scope `https://www.googleapis.com/auth/drive.file`) lädt der Dienst nach jedem Speichern (20 s entprellt) beide Dateien je Dokument in den Drive-Ordner `DOCS_DRIVE_FOLDER_ID` (leer: Ordner „IDA Docs“ wird angelegt). Fehler stören das Speichern nicht; der Stand steht in `GET /api/v1/backup` und als Chip auf der Startseite. Ohne die Werte ist das Backup aus.

## Einrichten

`DOCS_PORT` in der `.env` setzen (z. B. `10.7.0.1:8451`), `docker compose up -d docs`, im Tunnel eine Adresse (z. B. `docs.kulbarts.com`) auf diesen Port leiten und mit Cloudflare Access schützen (ohne Access gilt jeder als du).
