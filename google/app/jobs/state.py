"""Kleine SQLite-Datei im Volume: Zuordnung IDA-Kalender <-> Google und der Stand der Hintergrund-Aufgaben."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class State:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cal_map (
                    k_id TEXT PRIMARY KEY, g_id TEXT UNIQUE NOT NULL,
                    k_hash TEXT NOT NULL, g_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS job (name TEXT PRIMARY KEY, data TEXT NOT NULL);
                """
            )

    # -- Zuordnung ---------------------------------------------------------
    def pairs(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT k_id, g_id, k_hash, g_hash FROM cal_map").fetchall()
        return [{"k_id": r[0], "g_id": r[1], "k_hash": r[2], "g_hash": r[3]} for r in rows]

    def put_pair(self, k_id: str, g_id: str, k_hash: str, g_hash: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM cal_map WHERE g_id = ? AND k_id != ?", (g_id, k_id))
            self._db.execute(
                "INSERT INTO cal_map (k_id, g_id, k_hash, g_hash) VALUES (?,?,?,?) "
                "ON CONFLICT(k_id) DO UPDATE SET g_id=excluded.g_id, k_hash=excluded.k_hash, g_hash=excluded.g_hash",
                (k_id, g_id, k_hash, g_hash),
            )
            self._db.commit()

    def drop_pair(self, k_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM cal_map WHERE k_id = ?", (k_id,))
            self._db.commit()

    # -- Aufgaben-Stand ------------------------------------------------------
    def get_job(self, name: str) -> dict:
        with self._lock:
            row = self._db.execute("SELECT data FROM job WHERE name = ?", (name,)).fetchone()
        return json.loads(row[0]) if row else {}

    def set_job(self, name: str, data: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO job (name, data) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET data=excluded.data",
                (name, json.dumps(data, ensure_ascii=False, default=str)),
            )
            self._db.commit()
