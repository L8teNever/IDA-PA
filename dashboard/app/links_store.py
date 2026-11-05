"""Weiterleitungen (Kurzlinks): Ablage und Regeln. Geteilt vom IDA Server (verwaltet) und dem öffentlichen Dienst „links“
(leitet weiter). SQLite im WAL-Modus in einem gemeinsamen Volume (LINKS_DB) – beide Container greifen darauf zu.

Regeln je Link (alles optional, kombinierbar):
* max_uses       – wie oft er geöffnet werden darf (1 = Einmallink)
* valid_from     – erst ab diesem Zeitpunkt (Unix-Sekunden)
* valid_until    – nur bis zu diesem Zeitpunkt
* hours + hours_from – nur x Stunden aktiv, gezählt ab Erstellen („create“) oder ab dem ersten Öffnen („first“)
* device_mode    – any = jedes Gerät, once = jedes Gerät nur einmal, bind = nur das Gerät, das ihn zuerst geöffnet hat
* confirm        – erst eine Seite mit „Weiter“ (schützt vor Mail-Scannern, die Links vorab öffnen)
"""
import hashlib
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

# Kürzel: Kleinbuchstaben + Ziffern ohne Verwechsler (l/1/i/o/0) – gut abzutippen, Groß/klein egal.
# 7 Zeichen aus 31 = ~27 Mrd. Möglichkeiten; dazu bremst der öffentliche Dienst Durchprobieren (zu viele Fehlversuche → Pause).
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LEN = 7
CUSTOM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")  # eigenes Kürzel, z. B. „lebenslauf“
RESERVED = {"api", "robots.txt", "favicon.ico", "health"}
DEVICE_MODES = ("any", "once", "bind")

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  target TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  max_uses INTEGER,
  uses INTEGER NOT NULL DEFAULT 0,
  valid_from INTEGER,
  valid_until INTEGER,
  hours REAL,
  hours_from TEXT NOT NULL DEFAULT 'create',
  first_open INTEGER,
  last_open INTEGER,
  device_mode TEXT NOT NULL DEFAULT 'any',
  bound_device TEXT,
  confirm INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS visits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  link_id INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
  at INTEGER NOT NULL,
  device TEXT NOT NULL DEFAULT '',
  ok INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS visits_link ON visits(link_id, at);
CREATE TABLE IF NOT EXISTS link_devices (
  link_id INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
  device TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  first INTEGER NOT NULL,
  PRIMARY KEY (link_id, device)
);
"""

REASONS = {
    "disabled": "Dieser Link ist deaktiviert.",
    "not_yet": "Dieser Link ist noch nicht aktiv.",
    "expired": "Dieser Link ist abgelaufen.",
    "used_up": "Dieser Link wurde schon so oft geöffnet, wie er durfte.",
    "device_used": "Dieser Link wurde auf diesem Gerät schon geöffnet.",
    "other_device": "Dieser Link gehört zu einem anderen Gerät.",
}


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---------- lesen ----------
    def all(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM links ORDER BY created DESC")]

    def get(self, link_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM links WHERE id=?", (link_id,)).fetchone()
        return dict(r) if r else None

    def by_code(self, code: str) -> dict | None:
        r = self.db.execute("SELECT * FROM links WHERE code=?", (code.lower(),)).fetchone()
        return dict(r) if r else None

    def visits(self, link_id: int, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT at, device, ok, reason, country, agent FROM visits WHERE link_id=? ORDER BY id DESC LIMIT ?",
                                                 (link_id, limit))]

    def device_count(self, link_id: int) -> int:
        return self.db.execute("SELECT COUNT(*) FROM link_devices WHERE link_id=?", (link_id,)).fetchone()[0]

    # ---------- schreiben ----------
    def new_code(self, length: int = CODE_LEN) -> str:
        for n in range(40):
            code = "".join(secrets.choice(ALPHABET) for _ in range(length + n // 10))
            if not self.by_code(code):
                return code
        raise RuntimeError("Kein freies Kürzel gefunden")

    def create(self, fields: dict) -> dict:
        with self._lock:
            cols = ", ".join(fields)
            cur = self.db.execute(f"INSERT INTO links ({cols}, created) VALUES ({', '.join('?' * len(fields))}, ?)",
                                  (*fields.values(), int(time.time())))
            return self.get(cur.lastrowid)

    def update(self, link_id: int, fields: dict) -> dict | None:
        if fields:
            with self._lock:
                sets = ", ".join(f"{k}=?" for k in fields)
                self.db.execute(f"UPDATE links SET {sets} WHERE id=?", (*fields.values(), link_id))
        return self.get(link_id)

    def delete(self, link_id: int) -> bool:
        with self._lock:
            return self.db.execute("DELETE FROM links WHERE id=?", (link_id,)).rowcount > 0

    def reset(self, link_id: int) -> dict | None:
        """Zähler, erstes Öffnen, gebundenes Gerät und Geräte-Liste auf Anfang (Aufrufe bleiben im Verlauf)."""
        with self._lock:
            self.db.execute("UPDATE links SET uses=0, first_open=NULL, last_open=NULL, bound_device=NULL WHERE id=?", (link_id,))
            self.db.execute("DELETE FROM link_devices WHERE link_id=?", (link_id,))
        return self.get(link_id)

    # ---------- weiterleiten ----------
    def open(self, code: str, device: str, *, country: str = "", agent: str = "", now: int | None = None) -> tuple[dict | None, str]:
        """Ein echter Aufruf: prüft die Regeln und zählt – alles in einer Transaktion (zwei gleichzeitige Klicks auf einen
        Einmallink: nur einer kommt durch). Gibt (Link, "") oder (Link|None, Grund) zurück."""
        now = int(now or time.time())
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT * FROM links WHERE code=?", (code.lower(),)).fetchone()
                if not row:
                    self.db.execute("COMMIT")
                    return None, "unknown"
                link = dict(row)
                seen = self.db.execute("SELECT count FROM link_devices WHERE link_id=? AND device=?", (link["id"], device)).fetchone()
                reason = check(link, device, bool(seen), now)
                self.db.execute("INSERT INTO visits (link_id, at, device, ok, reason, country, agent) VALUES (?,?,?,?,?,?,?)",
                                (link["id"], now, device, 0 if reason else 1, reason, country[:8], agent[:300]))
                if not reason:
                    self.db.execute("""UPDATE links SET uses=uses+1, last_open=?, first_open=COALESCE(first_open, ?),
                                       bound_device=CASE WHEN device_mode='bind' AND bound_device IS NULL THEN ? ELSE bound_device END
                                       WHERE id=?""", (now, now, device, link["id"]))
                    self.db.execute("""INSERT INTO link_devices (link_id, device, count, first) VALUES (?,?,1,?)
                                       ON CONFLICT(link_id, device) DO UPDATE SET count=count+1""", (link["id"], device, now))
                    # alte Aufrufe ausdünnen: je Link die letzten 500
                    self.db.execute("DELETE FROM visits WHERE link_id=? AND id NOT IN (SELECT id FROM visits WHERE link_id=? ORDER BY id DESC LIMIT 500)",
                                    (link["id"], link["id"]))
                self.db.execute("COMMIT")
                return link, reason
            except Exception:
                self.db.execute("ROLLBACK")
                raise


def expires_at(link: dict) -> int | None:
    """Wann der Link spätestens endet (Bis-Datum und/oder x Stunden), None = nie. Bei „ab erstem Öffnen“ erst nach dem ersten Mal bekannt."""
    ends = []
    if link.get("valid_until"):
        ends.append(int(link["valid_until"]))
    if link.get("hours"):
        start = link["created"] if link.get("hours_from") != "first" else link.get("first_open")
        if start:
            ends.append(int(start + float(link["hours"]) * 3600))
    return min(ends) if ends else None


def check(link: dict, device: str, device_seen: bool, now: int) -> str:
    """Leerer String = darf weiter; sonst der Grund (Schlüssel aus REASONS)."""
    if not link["enabled"]:
        return "disabled"
    if link.get("valid_from") and now < link["valid_from"]:
        return "not_yet"
    end = expires_at(link)
    if end is not None and now >= end:
        return "expired"
    if link.get("max_uses") and link["uses"] >= link["max_uses"]:
        return "used_up"
    if link["device_mode"] == "once" and device_seen:
        return "device_used"
    if link["device_mode"] == "bind" and link.get("bound_device") and link["bound_device"] != device:
        return "other_device"
    return ""


def status(link: dict, now: int | None = None) -> str:
    """Für die Übersicht: active | scheduled | expired | used_up | disabled."""
    now = int(now or time.time())
    if not link["enabled"]:
        return "disabled"
    if link.get("valid_from") and now < link["valid_from"]:
        return "scheduled"
    end = expires_at(link)
    if end is not None and now >= end:
        return "expired"
    if link.get("max_uses") and link["uses"] >= link["max_uses"]:
        return "used_up"
    return "active"


def device_id() -> str:
    return secrets.token_urlsafe(18)


def short(device: str) -> str:
    """Kurze, stabile Geräte-Kennung für die Anzeige (nicht das Cookie selbst)."""
    return hashlib.sha256(device.encode()).hexdigest()[:6] if device else ""
