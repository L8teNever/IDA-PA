# IDA Launcher

Startseite mit **allen Apps** auf einen Blick, wie der Launcher von Cloudflare Access, aber im IDA-Stil (dunkel, mit Suche und Seiten-Symbolen).
Gedacht für `home.kulbarts.com`.

* **Automatisch**: Die Liste kommt aus Cloudflare. Quellen sind die **Access-Apps** (Zero Trust) und die **Adressen der Tunnel**.
  Kommt eine App oder Adresse dazu, erscheint sie nach spätestens einer Minute von selbst. Access-Apps mit ausgeschaltetem
  „im App Launcher zeigen“ bleiben draußen. Standardmäßig sind es nur die Access-Apps (wie im Cloudflare-Launcher). Mit `LAUNCHER_SHOW_UNPROTECTED=true` kommen auch Tunnel-Adressen
  ohne Access-App dazu, mit „offen“ markiert.
* **Zwei Bereiche**: Oben stehen die **IDA-Apps** (IDA, Kalender, Wiki, Code, Aufgaben, Dokumente, HTML, Dashboard, Server, Links, Launcher) mit schönen Namen,
  darunter alle **weiteren Apps**. IDA-App = Tunnel-Adresse, deren Ziel auf einem Port aus `IDA_PORTS` liegt (Standard 8440–8459);
  zeigen mehrere Adressen auf denselben Dienst (z. B. `cleo.` und `html.`), bleibt die bevorzugte. Zusätzlich mit `IDA_HOSTS`.
* **Suche**: Tippen filtert nach Name und Adresse, Enter öffnet den ersten Treffer, `/` springt ins Suchfeld, Esc leert es.
* **Symbole**: Zuerst das Logo der Access-App, sonst das Icon der Seite selbst. Seiten hinter Access sind von außen nicht lesbar,
  deshalb fragt der Launcher zuerst die interne Tunnel-Adresse (z. B. `http://10.7.0.1:8450`) ab und dann die öffentliche.
  Gefundene Symbole liegen 7 Tage im Volume `launcher-data`. Fehlt eins, steht der Anfangsbuchstabe da.
* **Akku**: Die Seite aktualisiert sich nur, solange sie sichtbar ist.

## Einrichten

1. `CLOUDFLARE_API_TOKEN` (derselbe wie beim Cloudflare-MCP, Lesen reicht: Access Apps and Policies Read, Cloudflare Tunnel Read, Account Read).
2. `docker compose pull && docker compose up -d launcher` (Port `LAUNCHER_PORT`, Standard 8093).
3. **Tunnel** `home.kulbarts.com` → `http://<Server>:<Port>` und **Cloudflare Access davor (Pflicht)**. Der Launcher hat keine eigene Anmeldung.

Einstellungen (in der `.env`): `LAUNCHER_HIDE` (Adressen ausblenden, `*` erlaubt), `LAUNCHER_EXTRA` (zusätzliche Einträge `Name=https://…`),
`LAUNCHER_SHOW_UNPROTECTED=true` (auch Tunnel-Adressen ohne Access-App zeigen).

## Technik

FastAPI (`launcher/app/`): `/api/v1/apps` (Liste), `/api/v1/icon/<host>` (nur für Adressen aus der Liste, kein beliebiger Abruf), `/api/v1/health`.
Oberfläche: `frontend/launcher/`. Tests: `cd launcher && python -m pytest`.
