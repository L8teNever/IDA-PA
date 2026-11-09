# IDA HTML – Code-Editor im Browser (früher CLEO)

IDA HTML ist ein Code-Editor mit Live-Vorschau (Monaco, HTML/CSS/JS, Python über Pyodide, ZIP-Import, Screenshot-Export). Übernommen aus dem Repo
`L8teNever/HTML` (dort lief es über Carla unter `/version/vN/`), jetzt als eigener Dienst im IDA-Stack und im IDA-Look.

- **Rein statisch:** `html/Dockerfile` liefert `frontend/html/` per nginx aus. Der Server speichert nichts.
- **Projekte liegen im Browser** (`localStorage`), also **pro Adresse getrennt**: `cleo.kulbarts.com` und `html.kulbarts.com` haben jeweils eigene Projekte.
- **Alte Projekte:** Früher waren sie pro Version gespeichert (`codeplay.files.v43`). Beim ersten Start an der neuen Adresse benutzt die App die neueste
  vorhandene Version direkt weiter (`cleo.storageVersion`). Kopiert wird nichts, weil `localStorage` nur rund 5 MB fasst.
  `/version/…` leitet auf `/` um.
- **Aussehen:** `frontend/html/css/ida.css` liegt über den Stilen der App und setzt nur das Aussehen. Das Theme `ida` steht in `THEMES` in `index.html`,
  die alten Themes sind in den Einstellungen weiter wählbar.

**Einrichten:** `HTML_PORT` in der `.env` setzen (z. B. `10.7.0.1:8450`), `docker compose up -d html`, im Tunnel `cleo.…` und `html.…` auf diesen Port leiten.

Die internen Namen `codeplay.*` und `cleo.*` im `localStorage` bleiben absichtlich, sonst wären die vorhandenen Projekte und Einstellungen weg.
