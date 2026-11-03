"""Messwerte in SQLite (data/dashboard.db).

Zwei Auflösungen in denselben Tabellen (Spalte `res`):
  0 = Einzelwerte alle `sample_seconds` (bleiben `raw_keep_hours`)
  1 = 10-Minuten-Mittel (bleiben `keep_days`), `n` = Anzahl der Einzelwerte darin (für Datenmengen)
Abfragen fassen auf höchstens ~`points` Punkte zusammen und nehmen dafür die passende Auflösung.
"""
import sqlite3
import threading
import time
from pathlib import Path

AGG = 600  # Sekunden je Mittelwert

HOST_COLS = ("cpu", "mem", "mem_total", "swap", "net_rx", "net_tx", "disk_used", "disk_total", "load1")
CTR_COLS = ("cpu", "mem", "net_rx", "net_tx", "blk_r", "blk_w")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS host (ts INTEGER NOT NULL, res INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 1,
  {", ".join(f"{c} REAL" for c in HOST_COLS)}, PRIMARY KEY (res, ts));
CREATE TABLE IF NOT EXISTS ctr (ts INTEGER NOT NULL, res INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 1, name TEXT NOT NULL, stack TEXT NOT NULL,
  {", ".join(f"{c} REAL" for c in CTR_COLS)}, PRIMARY KEY (res, name, ts));
CREATE INDEX IF NOT EXISTS ctr_ts ON ctr (res, ts);
CREATE INDEX IF NOT EXISTS ctr_stack ON ctr (res, stack, ts);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, path: Path, sample_seconds: int = 15, raw_keep_hours: int = 48, keep_days: int = 365):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.sample = sample_seconds
        self.raw_keep = raw_keep_hours * 3600
        self.keep = keep_days * 86400

    def close(self):
        self.db.close()

    # ---------- Schreiben ----------
    def add(self, ts: int, host: dict, ctrs: list[dict]) -> None:
        with self.lock:
            self.db.execute("BEGIN")
            self.db.execute(f"INSERT OR REPLACE INTO host (ts, res, {', '.join(HOST_COLS)}) VALUES (?, 0, {', '.join('?' * len(HOST_COLS))})",
                            (ts, *(host.get(c) for c in HOST_COLS)))
            self.db.executemany(f"INSERT OR REPLACE INTO ctr (ts, res, name, stack, {', '.join(CTR_COLS)}) VALUES (?, 0, ?, ?, {', '.join('?' * len(CTR_COLS))})",
                                [(ts, c["name"], c["stack"], *(c.get(k) for k in CTR_COLS)) for c in ctrs])
            self.db.execute("COMMIT")

    def compact(self, now: int | None = None) -> None:
        """Fertige 10-Minuten-Abschnitte zu Mittelwerten zusammenfassen und Altes löschen."""
        now = int(now or time.time())
        upto = now // AGG * AGG  # Beginn des laufenden Abschnitts – der bleibt noch offen
        with self.lock:
            row = self.db.execute("SELECT v FROM meta WHERE k = 'agg_upto'").fetchone()
            since = int(row[0]) if row else 0
            if upto <= since:
                return
            self.db.execute("BEGIN")
            avg = ", ".join(f"AVG({c})" for c in HOST_COLS)
            self.db.execute(f"""INSERT OR REPLACE INTO host (ts, res, n, {', '.join(HOST_COLS)})
                SELECT ts / {AGG} * {AGG}, 1, COUNT(*), {avg} FROM host WHERE res = 0 AND ts >= ? AND ts < ? GROUP BY ts / {AGG}""", (since, upto))
            avg = ", ".join(f"AVG({c})" for c in CTR_COLS)
            self.db.execute(f"""INSERT OR REPLACE INTO ctr (ts, res, n, name, stack, {', '.join(CTR_COLS)})
                SELECT ts / {AGG} * {AGG}, 1, COUNT(*), name, MAX(stack), {avg} FROM ctr WHERE res = 0 AND ts >= ? AND ts < ? GROUP BY ts / {AGG}, name""",
                            (since, upto))
            self.db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('agg_upto', ?)", (str(upto),))
            for t in ("host", "ctr"):
                self.db.execute(f"DELETE FROM {t} WHERE res = 0 AND ts < ?", (now - self.raw_keep,))
                self.db.execute(f"DELETE FROM {t} WHERE res = 1 AND ts < ?", (now - self.keep,))
            self.db.execute("COMMIT")

    # ---------- Lesen ----------
    def _plan(self, start: int, end: int, points: int, now: int) -> tuple[int, int]:
        """(Auflösung, Abstand der Punkte) für einen Zeitraum."""
        raw = start >= now - self.raw_keep
        step = max(self.sample if raw else AGG, -(-(end - start) // max(points, 1)))
        base = self.sample if raw else AGG
        return (0 if raw else 1), -(-step // base) * base

    def host_series(self, start: int, end: int, points: int = 300, now: int | None = None) -> dict:
        res, step = self._plan(start, end, points, int(now or time.time()))
        with self.lock:
            rows = self.db.execute(f"""SELECT ts / ? * ? AS b, {', '.join(f'AVG({c})' for c in HOST_COLS)}, MAX(cpu), MAX(mem),
                SUM(net_rx * n), SUM(net_tx * n) FROM host WHERE res = ? AND ts >= ? AND ts < ? GROUP BY b ORDER BY b""",
                                   (step, step, res, start, end)).fetchall()
        out = {"step": step, "res": res, "t": [r[0] for r in rows]}
        for i, c in enumerate(HOST_COLS, 1):
            out[c] = [_r(r[i]) for r in rows]
        k = len(HOST_COLS) + 1
        out["cpu_max"] = [_r(r[k]) for r in rows]
        out["mem_max"] = [_r(r[k + 1]) for r in rows]
        # übertragene Datenmenge im Zeitraum (Rate × Messabstand)
        out["total_rx"] = round(sum(r[k + 2] or 0 for r in rows) * self.sample)
        out["total_tx"] = round(sum(r[k + 3] or 0 for r in rows) * self.sample)
        return out

    def ctr_usage(self, start: int, end: int, now: int | None = None) -> list[dict]:
        """Verbrauch je Container im Zeitraum: Mittel, Spitze, Datenmenge."""
        res, _ = self._plan(start, end, 1, int(now or time.time()))
        with self.lock:
            rows = self.db.execute("""SELECT name, MAX(stack), AVG(cpu), MAX(cpu), AVG(mem), MAX(mem), SUM(net_rx * n), SUM(net_tx * n),
                SUM(blk_r * n), SUM(blk_w * n) FROM ctr WHERE res = ? AND ts >= ? AND ts < ? GROUP BY name""", (res, start, end)).fetchall()
        s = self.sample
        return [{"name": r[0], "stack": r[1], "cpu": _r(r[2]), "cpu_max": _r(r[3]), "mem": _r(r[4]), "mem_max": _r(r[5]),
                 "rx": round((r[6] or 0) * s), "tx": round((r[7] or 0) * s), "blk_r": round((r[8] or 0) * s), "blk_w": round((r[9] or 0) * s)}
                for r in rows]

    def ctr_series(self, start: int, end: int, metric: str, names: list[str] | None = None, group: str = "name",
                   points: int = 300, now: int | None = None) -> dict:
        """Verlauf einer Größe je Container (oder je Stack: group='stack', dann summiert)."""
        if metric not in CTR_COLS:
            raise ValueError("Unbekannte Größe")
        col = "stack" if group == "stack" else "name"
        res, step = self._plan(start, end, points, int(now or time.time()))
        where, args = "", []
        if names:
            where = f" AND {col} IN ({', '.join('?' * len(names))})"
            args = list(names)
        with self.lock:
            # erst je Container und Messzeitpunkt summieren (Stack = Summe seiner Container), dann pro Punkt mitteln
            rows = self.db.execute(f"""SELECT b, k, AVG(v) FROM (
                  SELECT ts / ? * ? AS b, ts, {col} AS k, SUM({metric}) AS v FROM ctr WHERE res = ? AND ts >= ? AND ts < ?{where} GROUP BY ts, k)
                GROUP BY b, k ORDER BY b""", (step, step, res, start, end, *args)).fetchall()
        ts = sorted({r[0] for r in rows})
        idx = {t: i for i, t in enumerate(ts)}
        series: dict[str, list] = {}
        for b, k, v in rows:
            series.setdefault(k, [None] * len(ts))[idx[b]] = _r(v)
        return {"step": step, "res": res, "t": ts, "series": series}

    def oldest(self) -> int | None:
        with self.lock:
            row = self.db.execute("SELECT MIN(ts) FROM host").fetchone()
        return row[0] if row else None


def _r(v):
    return None if v is None else round(v, 3)
