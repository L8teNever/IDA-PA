# IDA Docs – Dokumente schreiben

Eigene App (Dienst `docs`, Ordner `dokumente/` + `frontend/docs/`) für Aufsätze, Referate und Projektarbeiten: Markdown schreiben, daneben die Seitenvorschau in DIN A4.

- **Speicherung auf dem Server:** Jedes Dokument liegt im Volume `docs-data` zweimal: `docs/<id>.idadocs` (JSON mit Titel, Text, Einstellungen) und `docs/<id>.md` (nur der Text). Beide werden bei jedem Speichern zusammen geschrieben. Der Browser sichert zusätzlich lokal mit, falls die Verbindung weg ist, und lädt später hoch.
- **`.idadocs`:** Einstellungen (Ränder, Schrift, Abstände …) stehen nur drin, wenn sie vom Standard abweichen. Import und Export gehen mit `.idadocs` und `.md`.
- **Standard:** DIN A4, Ränder links 30 / rechts 20 / oben 25 / unten 20 mm, Arial 11 pt, Zeilenabstand 1,5, Blocksatz, H1/H2/H3 = 16/14/12 pt, nummerierte Überschriften, Seitenzahl unten Mitte. Alles pro Dokument einstellbar (Zahnrad-Knopf). Die Werte stehen in `DEFAULTS` in `dokumente/app/store.py` **und** `frontend/docs/js/app.js` und müssen gleich bleiben.
- **Ansichten:** „Seiten“ = echte A4-Blätter mit Seitenumbruch (Spalten-Layout: jede Spalte ist eine Seite), „Web“ = fortlaufend wie eine Webseite. Hell/Dunkel gilt für das Papier. PDF: „Als PDF speichern“ öffnet den Druckdialog (Ziel „Als PDF speichern“, Ränder „Keine“).
- **Markdown:** Überschriften, Listen (verschachtelt), fett/kursiv/durchgestrichen, Code, Zitate, Tabellen (mit `:--:`), Links, Bilder per Adresse, Fußnoten `[^1]`. Erweiterungen: `[TOC]` (Inhaltsverzeichnis mit Seitenzahlen), `\newpage`, `# Titel {-}` (ohne Nummer), `::: center|right|left|justify|title` … `:::`. Bausteine in der Werkzeugleiste: Titelblatt, Quellenverzeichnis, Selbstständigkeitserklärung.

- **Speichern:** automatisch ~0,7 s nach dem Tippen, beim Verlassen/Wegwischen der App (`keepalive`) und beim Zurückkommen des Netzes. Jeder Tastendruck landet sofort lokal im Browser; Dokumente, die offline geändert wurden, werden beim nächsten Öffnen der Liste auch ohne Aufruf hochgeladen.
- **Backups:** Dateien liegen lesbar im Volume (`docs/<id>.idadocs` + `.md`). Dazu Versionsstände in `versions/<id>/` (höchstens alle 10 Minuten, die letzten 40) und auf der Startseite „Backup (ZIP)“ (`GET /api/v1/backup`).
- **Notizen & Entwürfe:** `[[Notiz: …]]` erscheint nie im Dokument; `::: entwurf` … `:::` ist noch nicht fertiger Text (am Bildschirm gelb, im PDF normal, Warnung vor dem PDF-Export). Beides sammelt das Panel „Notizen & Entwürfe“ (Werkzeugleiste) mit Sprung zur Stelle.
- **Leere Zeilen:** Eine leere Zeile trennt Absätze wie üblich; jede weitere leere Zeile bleibt als Leerzeile im Dokument sichtbar.
- **Seitenmarker im Editor:** Im Markdown-Editor zeigt eine gestrichelte Linie „Seite N“ dort, wo im Dokument die nächste DIN-A4-Seite beginnt (bei langen Absätzen mitten im Absatz, beim ersten Wort der neuen Seite). Technik: `render()` setzt `data-l`/`data-e` (Markdown-Zeilen) an jeden Block, `pageStarts()` in `app.js` misst die Spalte (= Seite) und eine unsichtbare Spiegel-Ebene hinter dem Textfeld liefert die Zeilenhöhe (`drawMarks()`).
- **Ausrichtung:** Knöpfe links/zentriert/rechts setzen den markierten Text in `::: left|center|right` … `:::`.
- **Quellenarchiv:** Pro Dokument eine Liste von Quellen (Panel „Quellen“, in der `.idadocs` unter `sources`, nicht in der `.md`). Zitieren mit `[@schluessel]` oder `[@schluessel, 4]` (Kurzbeleg „(Müller 2020, S. 4)“ oder Fußnote, je nach Einstellung), `[QUELLEN]` erzeugt das Verzeichnis aus den zitierten Quellen (Stil „Deutsch“ oder APA). Das Panel zeigt, wie oft eine Quelle zitiert wird, und warnt bei Zitaten ohne Quelle (rot im Text). Pro Quelle gibt es eine Zitatsammlung („In Text einfügen“). „Ausfüllen“ holt Titel/Autor aus Webadresse (Seitentitel), ISBN (Open Library) oder DOI (Crossref); der Server ruft nur öffentliche Adressen ab (`dokumente/app/lookup.py`).

## Einrichten

`DOCS_PORT` in der `.env` setzen (z. B. `10.7.0.1:8451`), `docker compose up -d docs`, im Tunnel eine Adresse (z. B. `docs.kulbarts.com`) auf diesen Port leiten und mit Cloudflare Access schützen (ohne Access gilt jeder als du).
