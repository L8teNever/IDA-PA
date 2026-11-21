"""Geräte und Messwerte in SQLite (data/health.db).

Messwerte: eine schmale Zeile je Gerät und Zeitpunkt (WITHOUT ROWID, Primärschlüssel dev+res+ts – kompakt und schnell abzufragen).
  res 0 = Einzelwerte (höchstens alle `store_seconds`, bleiben `raw_keep_hours`)
  res 1 = 5-Minuten-Mittel (bleiben `keep_days`), cpu_max/temp_max = Spitzen darin
Details (je Kern, je Platte, je Netzwerkkarte, Lüfter) stehen nur im letzten Stand des Geräts – für den Verlauf reichen die Summen.
Geschrieben wird gesammelt (flush alle paar Sekunden in einer Transaktion), nicht je Nachricht.
"""
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

AGG = 300
COLS = ("cpu", "cpu_max", "mem", "mem_total", "swap", "load1", "temp", "temp_max", "rx", "tx", "rd", "wr", "disk_used", "disk_total")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, secret_hash TEXT NOT NULL, created INTEGER NOT NULL,
  last_seen INTEGER, info TEXT NOT NULL DEFAULT '{{}}', latest TEXT NOT NULL DEFAULT '{{}}', mesh_node TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS pair_codes (code_hash TEXT PRIMARY KEY, name TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL,
  mesh INTEGER NOT NULL DEFAULT 0, who TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS samples (dev TEXT NOT NULL, res INTEGER NOT NULL, ts INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 1,
  {", ".join(f"{c} REAL" for c in COLS)}, PRIMARY KEY (dev, res, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, dev TEXT NOT NULL, who TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS audit_dev ON audit (dev, ts);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

PAIR_TTL = 600


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def row_from_metrics(m: dict) -> dict:
    """Messwert-Nachricht des Agenten (siehe docs/HEALTH-PROTOKOLL.md) -> Spalten."""
    mem = m.get("mem") or [None, None]
    swap = m.get("swap") or [None, None]
    temps = [t[1] for t in m.get("temps") or [] if isinstance(t, list) and len(t) > 1 and isinstance(t[1], (int, float))]
    disks = [d for d in m.get("disks") or [] if isinstance(d, list) and len(d) > 2]
    net = [n for n in m.get("net") or [] if isinstance(n, list) and len(n) > 2]
    io = [d for d in m.get("io") or [] if isinstance(d, list) and len(d) > 2]
    load = m.get("load") or [None]
    cpu = m.get("cpu")
    return {
        "cpu": cpu, "cpu_max": cpu, "mem": mem[0], "mem_total": mem[1], "swap": swap[0], "load1": load[0] if load else None,
        "temp": max(temps) if temps else None, "temp_max": max(temps) if temps else None,
        "rx": sum(n[1] or 0 for n in net) if net else None, "tx": sum(n[2] or 0 for n in net) if net else None,
        "rd": sum(d[1] or 0 for d in io) if io else None, "wr": sum(d[2] or 0 for d in io) if io else None,
        "disk_used": sum(d[1] or 0 for d in disks) if disks else None, "disk_total": sum(d[2] or 0 for d in disks) if disks else None,
    }


class Store:
    def __init__(self, path: Path, store_seconds: int = 10, raw_keep_hours: int = 48, keep_days: int = 400):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.store_seconds = store_seconds
        self.raw_keep = raw_keep_hours * 3600
        self.keep = keep_days * 86400
        self._buf: list[tuple] = []
        self._last_ts: dict[str, int] = {}
        self._latest: dict[str, tuple[int, str]] = {}  # dev -> (last_seen, latest-JSON) – gesammelt geschrieben

    def close(self):
        self.flush()
        self.db.close()

    def _q(self, sql: str, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # ---------- Geräte ----------
    def create_pair_code(self, name: str, mesh: bool, who: str) -> str:
        code = "-".join(secrets.token_hex(3) for _ in range(3))
        now = int(time.time())
        with self.lock:
            self.db.execute("DELETE FROM pair_codes WHERE expires < ?", (now,))
            self.db.execute("INSERT INTO pair_codes (code_hash, name, created, expires, mesh, who) VALUES (?, ?, ?, ?, ?, ?)",
                            (_h(code), name[:60], now, now + PAIR_TTL, int(mesh), who))
        return code

    def redeem(self, code: str) -> dict | None:
        """Einmal-Code einlösen -> neues Gerät mit Secret (nur jetzt im Klartext)."""
        now = int(time.time())
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT name, mesh, who FROM pair_codes WHERE code_hash = ? AND expires >= ?", (_h(code), now)).fetchone()
            self.db.execute("DELETE FROM pair_codes WHERE code_hash = ? OR expires < ?", (_h(code), now))
            if not row:
                self.db.execute("COMMIT")
                return None
            dev_id, secret = secrets.token_hex(8), secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO devices (id, name, secret_hash, created) VALUES (?, ?, ?, ?)", (dev_id, row[0] or "Gerät", _h(secret), now))
            self.db.execute("COMMIT")
        return {"id": dev_id, "secret": secret, "name": row[0] or "Gerät", "mesh": bool(row[1]), "who": row[2]}

    def auth(self, dev_id: str, secret: str) -> bool:
        row = self._q("SELECT secret_hash FROM devices WHERE id = ?", (dev_id,))
        return bool(row) and secrets.compare_digest(row[0][0], _h(secret))

    def device(self, dev_id: str) -> dict | None:
        rows = self._q("SELECT id, name, created, last_seen, info, latest, mesh_node, notes FROM devices WHERE id = ?", (dev_id,))
        return self._dev(rows[0]) if rows else None

    def devices(self) -> list[dict]:
        return [self._dev(r) for r in self._q("SELECT id, name, created, last_seen, info, latest, mesh_node, notes FROM devices ORDER BY name COLLATE NOCASE")]

    def _dev(self, r) -> dict:
        d = {"id": r[0], "name": r[1], "created": r[2], "last_seen": r[3], "info": json.loads(r[4] or "{}"), "latest": json.loads(r[5] or "{}"),
             "mesh_node": r[6], "notes": r[7]}
        pend = self._latest.get(r[0])
        if pend:
            d["last_seen"], d["latest"] = pend[0], json.loads(pend[1])
        return d

    def find(self, key: str) -> dict | None:
        """Gerät per id oder Name (ohne Groß/klein)."""
        d = self.device(key)
        if d:
            return d
        rows = self._q("SELECT id FROM devices WHERE lower(name) = lower(?)", (key.strip(),))
        return self.device(rows[0][0]) if len(rows) == 1 else None

    def update_device(self, dev_id: str, **kw) -> None:
        allowed = {k: v for k, v in kw.items() if k in ("name", "info", "mesh_node", "notes", "last_seen")}
        if not allowed:
            return
        vals = [json.dumps(v) if k == "info" else v for k, v in allowed.items()]
        with self.lock:
            self.db.execute(f"UPDATE devices SET {', '.join(f'{k} = ?' for k in allowed)} WHERE id = ?", (*vals, dev_id))

    def delete_device(self, dev_id: str) -> None:
        with self.lock:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM devices WHERE id = ?", (dev_id,))
            self.db.execute("DELETE FROM samples WHERE dev = ?", (dev_id,))
            self.db.execute("COMMIT")
            self._buf = [b for b in self._buf if b[0] != dev_id]
            self._latest.pop(dev_id, None)
            self._last_ts.pop(dev_id, None)

    # ---------- Einstellungen (im Browser gespeichert) ----------
    def overrides(self) -> dict:
        return {k: json.loads(v) for k, v in self._q("SELECT k, v FROM settings")}

    def save_overrides(self, set_: dict, reset: list[str]) -> None:
        with self.lock:
            self.db.execute("BEGIN")
            self.db.executemany("INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)", [(k, json.dumps(v)) for k, v in set_.items()])
            self.db.executemany("DELETE FROM settings WHERE k = ?", [(k,) for k in reset])
            self.db.execute("COMMIT")

    # ---------- Protokoll ----------
    def audit(self, dev: str, who: str, action: str, detail: str = "") -> None:
        with self.lock:
            self.db.execute("INSERT INTO audit (ts, dev, who, action, detail) VALUES (?, ?, ?, ?, ?)", (int(time.time()), dev, who or "?", action, detail[:2000]))

    def audit_list(self, dev: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT ts, dev, who, action, detail FROM audit" + (" WHERE dev = ?" if dev else "") + " ORDER BY id DESC LIMIT ?"
        return [{"ts": r[0], "dev": r[1], "who": r[2], "action": r[3], "detail": r[4]} for r in self._q(q, ((dev,) if dev else ()) + (limit,))]

    # ---------- Messwerte ----------
    def add(self, dev: str, m: dict) -> None:
        """Merkt den Messwert vor (Schreiben passiert gesammelt in flush)."""
        ts = int(m.get("ts") or time.time())
        now = int(time.time())
        if abs(ts - now) > 900:  # Uhr des Geräts falsch -> Zeit von Health
            ts = now
        self._latest[dev] = (now, json.dumps(m, separators=(",", ":")))
        if ts - self._last_ts.get(dev, 0) < self.store_seconds:
            return
        self._last_ts[dev] = ts
        r = row_from_metrics(m)
        self._buf.append((dev, ts, *(r[c] for c in COLS)))

    def flush(self) -> int:
        with self.lock:
            buf, self._buf = self._buf, []
            latest, self._latest = self._latest, {}
            if not buf and not latest:
                return 0
            self.db.execute("BEGIN")
            self.db.executemany(f"INSERT OR REPLACE INTO samples (dev, res, ts, {', '.join(COLS)}) VALUES (?, 0, ?, {', '.join('?' * len(COLS))})", buf)
            self.db.executemany("UPDATE devices SET last_seen = ?, latest = ? WHERE id = ?", [(t, j, d) for d, (t, j) in latest.items()])
            self.db.execute("COMMIT")
        return len(buf)

    def compact(self, now: int | None = None) -> None:
        """Fertige 5-Minuten-Abschnitte mitteln, Altes löschen."""
        now = int(now or time.time())
        upto = now // AGG * AGG
        with self.lock:
            row = self.db.execute("SELECT v FROM meta WHERE k = 'agg_upto'").fetchone()
            since = int(row[0]) if row else 0
            if upto <= since:
                return
            avg = ", ".join(f"MAX({c})" if c in ("cpu_max", "temp_max") else f"AVG({c})" for c in COLS)
            self.db.execute("BEGIN")
            self.db.execute(f"""INSERT OR REPLACE INTO samples (dev, res, ts, n, {', '.join(COLS)})
                SELECT dev, 1, ts / {AGG} * {AGG}, COUNT(*), {avg} FROM samples WHERE res = 0 AND ts >= ? AND ts < ? GROUP BY dev, ts / {AGG}""",
                            (since, upto))
            self.db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('agg_upto', ?)", (str(upto),))
            self.db.execute("DELETE FROM samples WHERE res = 0 AND ts < ?", (now - self.raw_keep,))
            self.db.execute("DELETE FROM samples WHERE res = 1 AND ts < ?", (now - self.keep,))
            self.db.execute("COMMIT")

    def series(self, dev: str, start: int, end: int, points: int = 300, now: int | None = None) -> dict:
        now = int(now or time.time())
        raw = start >= now - self.raw_keep
        base = self.store_seconds if raw else AGG
        step = max(base, -(-(end - start) // max(points, 1)))
        step = -(-step // base) * base
        res = 0 if raw else 1
        agg = ", ".join(f"MAX({c})" if c in ("cpu_max", "temp_max") else f"AVG({c})" for c in COLS)
        rows = self._q(f"SELECT ts / ? * ? AS b, {agg}, SUM(rx * n), SUM(tx * n) FROM samples WHERE dev = ? AND res = ? AND ts >= ? AND ts < ? GROUP BY b ORDER BY b",
                       (step, step, dev, res, start, end))
        out = {"step": step, "res": res, "t": [r[0] for r in rows]}
        for i, c in enumerate(COLS, 1):
            out[c] = [None if r[i] is None else round(r[i], 2) for r in rows]
        k = len(COLS) + 1
        per = self.store_seconds if raw else self.store_seconds  # n zählt Einzelwerte
        out["total_rx"] = round(sum(r[k] or 0 for r in rows) * per)
        out["total_tx"] = round(sum(r[k + 1] or 0 for r in rows) * per)
        return out
