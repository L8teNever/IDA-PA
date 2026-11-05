# IDA Server – einrichten statt nur ansehen

Der **IDA Server** (`server.kulbarts.com`) ist dieselbe App wie das [Dashboard](DASHBOARD.md), nur im Modus `APP_MODE=server`
(gleiches Image `ida-dashboard`, eigener Container `server` im IDA-Stack, eigene PWA „IDA Server“).

* **Dashboard** = Überblick: Übersicht, Stacks, Dateien, Leistung, Speicher. Bleibt, wie es ist.
* **Server** = einrichten: **Websites**, **Veröffentlichen**, **Stacks** und **Dateien**. Stacks und Dateien sind derselbe Code
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

## Veröffentlichen

Alle Adressen der (remote verwalteten) Cloudflare-Tunnel mit Ziel, zugehörigem Stack und Schutz (Zero Trust / öffentlich).

* **Neue Adresse** in einem Schritt: Tunnel-Regel (Hostname[/Pfad] → Ziel), DNS-CNAME auf den Tunnel, optional Zero Trust.
  Als Ziel schlägt der Server jeden Container-Port vor, der auf dem Server veröffentlicht ist (`http://<Server-IP>:<Port>`).
  Die Server-IP kommt aus `TUNNEL_HOST_IP` oder den vorhandenen Regeln (z. B. `10.7.0.1`).
* **Ziel ändern**, **Zero Trust an/aus**, **entfernen**. Der DNS-Eintrag wird nur gelöscht, wenn sonst nichts mehr auf den Host
  zeigt und er auf diesen Tunnel zeigt.

## Einrichtung

1. `.env` (siehe `.env.example`):
   ```ini
   SERVER_PUBLIC_URL=https://server.kulbarts.com
   SITES_PORT=10.7.0.1:8087            # nginx der Websites – an die IP, über die cloudflared den Server erreicht
   CLOUDFLARE_API_TOKEN=…              # derselbe wie beim Cloudflare-MCP, siehe DASHBOARD.md
   # SERVER_PORT=8088  SITES_DIR=/opt/ida-sites  SITES_UPSTREAM=  TUNNEL_HOST_IP=
   ```
   Rechte des Tokens für den Server: **Cloudflare Tunnel Edit**, **DNS Edit** (für die Zonen), **Access: Apps and Policies Edit**.
2. **Tunnel**: `server.kulbarts.com` → `http://<server>:8088`, und **Cloudflare Access davor (Pflicht)**: Der Server hat den
   Docker-Socket und einen Cloudflare-Token mit Schreibrecht. Eine eigene Anmeldung gibt es nicht.
3. `docker compose pull && docker compose up -d server sites`

Wie der Tunnel die Websites erreicht: `SITES_UPSTREAM`, sonst der veröffentlichte Port des nginx `sites` mit seiner IP (bei
`0.0.0.0` mit der Server-IP aus den Tunnel-Regeln). Klappt das nicht, sagt es das Tool Websites oben.
