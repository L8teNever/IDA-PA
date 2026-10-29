# Wiki (XWiki) für dein Leben

Läuft im selben Stack wie IDA (Profil `wiki`) und holt immer das neueste Image der stabilen XWiki-Reihe.
IDA schreibt jeden Chat als Seite nach **Ida › Chats**.

## Einschalten

In die `.env`:

```
COMPOSE_PROFILES=wiki
XWIKI_DB_PASSWORD=<zufällig>
XWIKI_PORT=8081                # oder 10.7.0.1:8441, wenn nur der Tunnel drauf soll
WIKI_URL=http://xwiki:8080
WIKI_USER=Ida
WIKI_PASSWORD=<zufällig>
IDA_PUBLIC_URL=https://ida.example.com
```

Dann `docker compose up -d`. Der erste Start dauert ein paar Minuten.

## Einrichten (einmal)

1. Wiki im Browser öffnen. Der Assistent fragt nach deinem Admin-Konto und installiert die
   „XWiki Standard Flavor“ (dauert einige Minuten) – danach „Weiter“ bis zum Ende.
2. Struktur und IDAs Benutzer anlegen, mit deinem Admin-Login:

   ```
   docker compose exec app python -m app.cli wiki-setup <admin> <passwort>
   ```

   Das legt **Mein Leben** mit den Bereichen und Vorlagen an, IDAs Wiki-Benutzer, und schreibt alle bisherigen Chats
   ins Wiki. Vorhandene Seiten bleiben unangetastet – der Befehl darf jederzeit nochmal laufen.

## Aufbau

- **Leben** – die Bereiche (Ich, Menschen, Gesundheit, Schule & Arbeit, Finanzen, Zuhause, Projekte, Wissen,
  Orte & Reisen, Ziele & Ideen, Tagebuch). Jeder Bereich hat einen Knopf **„＋ Neuer Eintrag“**, der eine Seite
  aus der Vorlage des Bereichs anlegt – so ist jede Seite gleich aufgebaut und landet am richtigen Platz.
- **Vorlagen** – die Vorlagen der Bereiche. Änderst du eine, sehen neue Einträge ab da so aus.
  Neue Bereiche kommen in `backend/app/services/wiki_setup.py` (`AREAS`) dazu, dann `wiki-setup` nochmal laufen lassen.
- **Ida › Chats** – von IDA geschrieben, nach jeder Antwort neu. Nicht von Hand ändern (wird überschrieben).
  In IDA gelöschte Chats bleiben hier als Archiv.

## Updates und Sicherung

`docker compose pull && docker compose up -d` (oder Watchtower, Profil `autoupdate`) holt auch das Wiki-Image.
Vor größeren XWiki-Updates die Volumes `xwiki-data` und `xwiki-db` sichern.
