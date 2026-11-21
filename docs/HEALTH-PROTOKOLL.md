# Protokoll ida-connect ↔ IDA Health

Eine einzige WebSocket-Verbindung, die **das Gerät aufbaut** (ausgehend, kein offener Port nötig):
`wss://<health>/api/v1/agent/ws`. Alle Nachrichten sind JSON-Textframes mit dem Feld `t` (Typ).
Dieselbe Datei liegt in github.com/L8teNever/Ida-connect als `PROTOCOL.md` – Änderungen immer in beiden.

## Anmeldung

Header `Authorization` beim Verbindungsaufbau:

* `Bearer <device_id>:<secret>` – normales Gerät. Health speichert nur SHA-256 des Secrets.
* `Pair <code>` – Erstverbindung mit einem Einmal-Code (gültig 10 Minuten, einmal nutzbar).
  Health antwortet als erste Nachricht `{"t":"paired","id":"…","secret":"…","name":"…"}`; das Gerät speichert beides
  und läuft danach in derselben Verbindung normal weiter.

Falsche Anmeldung: Health schließt mit Code 4401 (Gerät gelöscht/Secret falsch → nicht ständig neu versuchen, nur alle 5 Minuten).

## Gerät → Health

| t | Inhalt |
|---|---|
| `hello` | `v` (Protokoll 1), `agent` (Version), `info` (s. u.) – erste Nachricht nach dem Aufbau |
| `info` | `info` – wenn sich etwas ändert (z. B. Mesh), sonst alle 10 Minuten |
| `m` | Messwerte (s. u.), alle `interval` Sekunden |
| `res` | Antwort auf `req`: `id`, `ok`, `data` oder `error` |
| `pty_out` | `sid`, `d` (Base64) |
| `pty_exit` | `sid`, `code` |

`info`: `hostname, os, kernel, arch, cpu_model, cores, mem_total, mode (systemd), mesh {ip, status}, boot (Unix-Zeit),
route ("mesh" | "public" – über welchen Weg das Gerät gerade verbunden ist), auto_update (bool), update {current, latest, available}`

`m` (kompakt, Bytes/Bytes pro Sekunde, °C, U/min):
```json
{"t":"m","ts":1763700000,"cpu":12.5,"cores":[10.1,14.9],"mem":[used,total],"swap":[used,total],
 "load":[0.3,0.2,0.1],"up":12345,"procs":180,
 "temps":[["cpu",48.2]],"fans":[["fan1",1200]],
 "disks":[["/",used,total]],"net":[["eth0",rx_bps,tx_bps]],"io":[["mmcblk0",read_bps,write_bps]]}
```

## Health → Gerät

| t | Inhalt |
|---|---|
| `config` | `interval` (Sekunden, 1–300) – z. B. 10 normal, 2 solange jemand live zuschaut; `mesh_server` (Adresse von Health im Cloudflare Mesh, z. B. `http://100.96.0.5:8000`, leer = nicht im Mesh; fehlt das Feld, bleibt der alte Wert) |
| `req` | `id`, `op`, weitere Felder je nach `op` (s. u.) |
| `pty_open` | `sid`, `cols`, `rows` – neue Shell (bash -l, sonst sh) als root auf dem Gerät |
| `pty_in` | `sid`, `d` (Base64) |
| `pty_resize` | `sid`, `cols`, `rows` |
| `pty_close` | `sid` |
| `ping` | Gerät antwortet `{"t":"pong"}` |

### Operationen (`req.op`)

| op | Felder | `data` |
|---|---|---|
| `exec` | `cmd`, `timeout` (s, max 3600), `user?` | `code, stdout, stderr, timeout (bool)` |
| `fs_list` | `path` | `path, entries: [[name, typ d/f/l, size, mtime, mode]]` |
| `fs_read` | `path`, `offset?`, `len?` (max 4 MB) | `size, d (Base64), eof` |
| `fs_write` | `path`, `d` (Base64), `append?`, `mkdirs?` | `size` |
| `fs_delete` | `path`, `recursive?` | – |
| `fs_mkdir` | `path` | – |
| `fs_rename` | `from`, `to` | – |
| `mesh_setup` | `token` | `output` – installiert cloudflare-warp und meldet das Gerät als Mesh-Knoten an |
| `mesh_status` | – | `ip, status` |
| `update` | – | startet das Update von ida-connect (Install-Skript in eigener systemd-Unit; Kopplung bleibt, bei Fehlstart zurück zur alten Version) |
| `update_check` | – | `current, latest, available, checked, error` – fragt GitHub (`…/releases/latest`) |
| `set_auto_update` | `on` (bool) | `auto_update` – neue Versionen selbst installieren (alle 6 Stunden nachsehen) |
| `info` | – | wie `info` |

Pfade sind immer absolute Pfade auf dem Gerät.


## Weg der Verbindung (Mesh oder öffentlich)

Hat Health eine Mesh-Adresse geschickt (`config.mesh_server`) und ist WARP auf dem Gerät verbunden, verbindet sich der Agent zuerst
über das Mesh (6 s Zeitlimit). Klappt das nicht, sofort über die öffentliche Adresse (`server`) und 5 Minuten lang nicht wieder über
das Mesh. Ist das Gerät über die öffentliche Adresse verbunden, prüft es alle 5 Minuten per TCP, ob Health im Mesh erreichbar ist,
und wechselt dann. Über die Mesh-IP nimmt Health nur `/api/v1/agent/*` an.
