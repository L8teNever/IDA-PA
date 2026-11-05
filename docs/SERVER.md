# IDA Server – einrichten statt nur ansehen

Der **IDA Server** (`server.kulbarts.com`) ist dieselbe App wie das [Dashboard](DASHBOARD.md), nur im Modus `APP_MODE=server`
(gleiches Image `ida-dashboard`, eigener Container `server` im IDA-Stack, eigene PWA „IDA Server“).

* **Dashboard** = Überblick: Übersicht, Stacks, Graph, Veröffentlichen, Dateien, Leistung, Speicher.
* **Server** = einrichten: **Websites**, **Weiterleitungen**, **Stacks**, **Updates** und **Dateien**. Stacks und Dateien sind derselbe Code
  wie im Dashboard, also genau gleich. Die Messwerte (CPU/RAM je Container, Verlauf) holt der Server beim Dashboard
  (`DASHBOARD_URL`) und misst nicht doppelt.

## Websites (Nachfolger des Seiten-Hostings aus Carla)

Ein nginx-Dienst `sites` im IDA-Stack liefert alle Seiten aus. Die Dateien liegen in `SITES_DIR/www/<name>` (Standard
`/opt/ida-sites`), die Konfiguration schreibt der Server nach `SITES_DIR/conf/default.conf`.

| Was | Wie |
|---|---|
| Adresse | Hostname, optional mit Pfad (`kulbarts.com/blog`). Mehrere Seiten können sich einen Host teilen (Pfade). |
| Inhalt | **GitHub-Repo** (Branch, optional Token für private Repos) oder **eigene Dateien** (Willkommensseite, dann im Tool Dateien bearbeiten) |
| Ordner | Welcher Ordner ausgeliefert wird, z. B. `dist`, `build` oder `public` (leer = alles) |
| SPA-Modus | Unbekannte Routen liefern `index.html` (React, Vue …) |
| Auto-Update | Aus, alle 5/15 Minuten, stündlich, täglich – sonst „Aktualisieren“ per Knopf |
| Zugriff | Öffentlich oder **Zero Trust** (Access-App mit erlaubten E-Mails, `@domain.de` für eine ganze Domain) |

Was gegenüber Carla besser ist:

* **Nichts geht kaputt:** Vor jedem Neuladen prüft `nginx -t` die neue Konfiguration. Lehnt nginx sie ab, kommt die alte zurück.
* **Token bleibt geheim:** Der GitHub-Token landet nie in `.git/config` und wird in jeder Ausgabe ausgeblendet. Er liegt nur in
  `/data/sites.json` (Rechte 600).
* **Aktualisieren** ist `fetch` + `reset --hard` + `clean`: Der Stand entspricht genau dem Branch.
* **Dotfiles** (`.git`, `.env` …) liefert nginx nie aus (außer `.well-known`).
* **Veröffentlichen läuft automatisch:** Anlegen macht Tunnel-Regel, DNS-CNAME und auf Wunsch die Access-App. Zeigt der Host
  schon woanders hin, bekommt nur der Pfad eine eigene Regel. **Entfernen** räumt nur auf, was die Seite selbst angelegt hat.
* Alle Vorgänge laufen mit **Live-Ausgabe** (wie die Stack-Aktionen).

Noch nicht übernommen aus Carla: PHP-Seiten, Einmallinks (Token-Gate) und Fehlerseiten. Das lässt sich bei Bedarf nachrüsten.

## Weiterleitungen (Kurzlinks)

Im Tool **Weiterleitungen** legst du Links an, z. B. `l.kulbarts.com/k7m2xqp`. Jedes Kürzel funktioniert auf allen vier Domains
(`l.kulbarts.com`, `link.kulbarts.com`, `l.l8tenever.com`, `link.l8tenever.com`, änderbar über `LINKS_DOMAINS`).

* **Kürzel:** 7 Zeichen aus Kleinbuchstaben und Zahlen ohne Verwechsler (`l`, `1`, `i`, `o`, `0`), rund 27 Milliarden Möglichkeiten.
  Groß/klein ist egal. Nach 20 unbekannten Kürzeln in 10 Minuten bekommt eine IP eine Pause. Eigene Kürzel sind möglich.
* **Wie oft:** unbegrenzt, einmal oder x-mal.
* **Wie lange:** immer, bis Datum, Zeitraum, oder x Stunden ab Erstellen oder ab dem ersten Öffnen.
* **Geräte:** alle, jedes nur einmal, oder nur das Gerät, das ihn zuerst geöffnet hat.
* **Verwalten:** an/aus, Zähler zurücksetzen (auch das gebundene Gerät), Aufrufe ansehen, löschen.

Aufbau: Der Server verwaltet, der eigene Container `links` (`APP_MODE=links`) leitet weiter. Nur `links` ist öffentlich, er hat weder
Docker-Socket noch Cloudflare-Token. Beide teilen sich die SQLite-Datenbank im Volume `links-data`. **Domains einrichten** im Tool legt
Tunnel-Regel und DNS an, bewusst ohne Zero Trust. Adressen, die schon woanders hinzeigen, bleiben unverändert.

Gut zu wissen:

* **Vorschau-Bots** (WhatsApp, Telegram …) und `HEAD`-Aufrufe sehen nur eine neutrale Seite und zählen nicht. Sonst wäre ein Einmallink
  verbraucht, bevor jemand draufklickt. Der Link wird dabei nicht verraten.
* **Mail-Scanner** tarnen sich oft als Browser. Dagegen hilft die Option „Weiter“-Seite: gezählt wird erst beim Klick (POST).
* **Geräte** erkennt der Dienst am Cookie `ida_dev`. Ein Cookie gilt nur je Domain, deshalb springen Links mit Geräte-Regel erst auf
  ihre angezeigte Adresse. Ein privates Fenster, ein anderer Browser oder gelöschte Cookies zählen als neues Gerät: Das hält
  normale Nutzung auf, aber niemanden, der es gezielt umgehen will. Hat das gebundene Gerät sein Cookie verloren, hilft „Zähler zurücksetzen“.

## Belegte Adressen im Gedächtnis

Jede Website und jeder Kurzlink bekommt eine eigene Seite im Wiki-Gedächtnis unter `memory/projekte/domains/`, mit Adresse, Ziel und
Erstellungsdatum. So können alle KIs beantworten, welche Domains und Subdomains belegt sind. Wird die Website oder der Link gelöscht,
verschwindet auch die Seite (im Wiki-Papierkorb bleibt sie 30 Tage).

Der Server gleicht das nach jeder Änderung und alle 10 Minuten ab. Dadurch kommen auch ältere Websites hinein, und war das Wiki kurz weg,
wird es nachgeholt. Von Link-Zielen landen nur Adresse und Pfad im Gedächtnis, keine Parameter, weil dort oft Tokens stehen.
Eigene Seiten im Ordner bleiben unangetastet: Der Abgleich löscht nur `website-*` und `link-*`. Adressen aus „Veröffentlichen“ sind nicht dabei.

## Updates (neue Docker-Images finden und einspielen)

Das Tool sucht für alle laufenden Container nach neuen Versionen. Ohne eigene Einstellung sucht es alle 6 Stunden und zeigt nur an,
aktualisiert wird dann nichts. Unter **Automatik** stellt man ein:

| Frage | Möglichkeiten |
|---|---|
| Wann suchen? | Regelmäßig (1 bis 24 Stunden), täglich um eine Uhrzeit, nur per Knopf |
| Was bei einem Fund? | Nur anzeigen, sofort aktualisieren, zu einer festen Uhrzeit aktualisieren |
| Für welche Dienste? | Alle, alle außer …, nur diese (ganze Stacks oder einzelne Dienste) |

„Nachts um 3 nachsehen und aktualisieren“ ist also: täglich um 03:00 + sofort. War der Server zur Uhrzeit aus, zählt sie noch eine
Stunde lang, danach kommt die nächste Nacht. Von Hand geht jedes Update unabhängig von diesen Regeln.

**Gesucht** wird mit einem HEAD auf das Manifest in der Registry (`app/registry.py`): Das lädt nichts und zählt bei Docker Hub nicht
ins Pull-Limit. Logins kommen aus `~/.docker/config.json`.

**Aktualisiert** wird `pull` + `up -d --no-deps` nur für die gewählten Dienste. Dabei gilt:

* **Nur laufende Compose-Dienste.** Gestoppte Container bleiben aus, ein Update würde sie starten. Container ohne Compose werden nur
  angezeigt.
* **Lokal gebaute Images** (`build:` oder ein Name, den keine Registry kennt) werden nie angefasst. Ihr Name könnte in einer Registry
  ein fremdes Image sein.
* **Telefonate:** Das Label `com.centurylinklabs.watchtower.lifecycle.pre-update` gilt auch hier. Exit 75 heißt „gerade nicht“, die
  Automatik versucht es alle 10 Minuten bis zu sechsmal und sonst bei der nächsten Runde wieder.
* **`network_mode: service:x`:** Dienste, die am Netz eines aktualisierten Dienstes hängen, werden mit neu erstellt.
* **Nach dem Start** wird bis zu 90 Sekunden geprüft, ob der neue Container sauber läuft. Wenn nicht, steht das im Verlauf, das alte
  Image bleibt liegen, und die Automatik versucht diese Version nicht noch einmal. Ein automatisches Zurück gibt es nicht.
* **Der Server selbst** kommt zuletzt dran: Ein kurzer Hilfscontainer aus dem neuen Image erstellt ihn neu.
* **Alte Images** werden nur gelöscht, wenn alles sauber läuft und kein anderer Container sie nutzt (abschaltbar).

Einstellungen, letzte Suche und Verlauf liegen in `/data/updates.json`. Watchtower (Profil `autoupdate`) sollte nicht parallel laufen,
das Tool warnt dann.

## Einrichtung

1. `.env` (siehe `.env.example`):
   ```ini
   SERVER_PUBLIC_URL=https://server.kulbarts.com
   SITES_PORT=10.7.0.1:8087            # nginx der Websites – an die IP, über die cloudflared den Server erreicht
   CLOUDFLARE_API_TOKEN=…              # derselbe wie beim Cloudflare-MCP, siehe DASHBOARD.md
   LINKS_PORT=10.7.0.1:8089            # öffentlicher Dienst der Kurzlinks – ebenfalls an diese IP
   # SERVER_PORT=8088  SITES_DIR=/opt/ida-sites  SITES_UPSTREAM=  TUNNEL_HOST_IP=  LINKS_UPSTREAM=
   ```
   Rechte des Tokens für den Server: **Cloudflare Tunnel Edit**, **DNS Edit** (für die Zonen), **Access: Apps and Policies Edit**.
2. **Tunnel**: `server.kulbarts.com` → `http://<server>:8088`, und **Cloudflare Access davor (Pflicht)**: Der Server hat den
   Docker-Socket und einen Cloudflare-Token mit Schreibrecht. Eine eigene Anmeldung gibt es nicht.
3. `docker compose pull && docker compose up -d server sites links`

Wie der Tunnel die Websites erreicht: `SITES_UPSTREAM`, sonst der veröffentlichte Port des nginx `sites` mit seiner IP (bei
`0.0.0.0` mit der Server-IP aus den Tunnel-Regeln). Klappt das nicht, sagt es das Tool Websites oben.
