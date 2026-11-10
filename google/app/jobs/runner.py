"""Fuehrt die Hintergrund-Aufgaben aus (Kalender-Abgleich alle paar Minuten, Docs-Backup einmal taeglich) und merkt sich den Stand."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.google_client import GoogleApiClient, GoogleApiError
from app.jobs import calendar_sync as cs
from app.jobs import docs_backup as db
from app.jobs.state import State

log = logging.getLogger("ida-google.jobs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Runner:
    def __init__(self, settings, client: GoogleApiClient, google) -> None:
        self.s, self.client, self.google = settings, client, google
        self.state = State(f"{settings.data_dir.rstrip('/')}/hub.db")
        self._locks = {"calendar": asyncio.Lock(), "backup": asyncio.Lock()}

    # -- Stand ---------------------------------------------------------------
    def calendar_status(self) -> dict:
        return {"aktiv": self.s.calendar_sync, "intervall_s": self.s.sync_interval_s, **self.state.get_job("calendar")}

    def backup_status(self) -> dict:
        return {"aktiv": self.s.docs_backup, "stunde": self.s.docs_backup_hour, "behalten": self.s.docs_backup_keep, **self.state.get_job("backup")}

    # -- Kalender --------------------------------------------------------------
    def _calendar_sync(self) -> cs.CalendarSync:
        s = self.s
        return cs.CalendarSync(cs.KalenderApi(s.kalender_url, s.kalender_token), cs.GoogleCal(self.client, s.google_calendar_id), self.state,
                               calendar=s.kalender_calendar, timezone_name=s.timezone, past_days=s.sync_past_days,
                               future_days=s.sync_future_days, max_deletes=s.sync_max_deletes)

    async def run_calendar(self, dry_run: bool = False) -> dict:
        if self._locks["calendar"].locked():
            return {"ok": False, "fehler": "Ein Abgleich laeuft gerade"}
        async with self._locks["calendar"]:
            try:
                rep = await asyncio.to_thread(lambda: self._calendar_sync().run(dry_run=dry_run))
                out = {"ok": not rep["errors"], **rep}
            except (cs.SyncAbort, GoogleApiError) as exc:
                out = {"ok": False, "fehler": str(exc)}
            except Exception as exc:  # noqa: BLE001 – ein Fehler darf die Schleife nie beenden
                log.exception("Kalender-Abgleich")
                out = {"ok": False, "fehler": f"Unerwarteter Fehler: {exc}"}
            if not dry_run:
                self.state.set_job("calendar", {"zuletzt": _now(), "letztes_ergebnis": {k: v for k, v in out.items() if k != "plan"}})
            return out

    # -- Backup ---------------------------------------------------------------
    async def run_backup(self) -> dict:
        if self._locks["backup"].locked():
            return {"ok": False, "fehler": "Eine Sicherung laeuft gerade"}
        async with self._locks["backup"]:
            try:
                res = await asyncio.to_thread(db.run_backup, db.Drive(self.client), self.s.docs_url, self.s.docs_backup_folder, self.s.docs_backup_keep)
                out = {"ok": True, **res}
            except GoogleApiError as exc:
                out = {"ok": False, "fehler": str(exc)}
            except Exception as exc:  # noqa: BLE001
                log.exception("Docs-Backup")
                out = {"ok": False, "fehler": f"Unerwarteter Fehler: {exc}"}
            prev = self.state.get_job("backup")
            self.state.set_job("backup", {"zuletzt": _now(), "letztes_ergebnis": out, "letzter_erfolg": _now() if out["ok"] else prev.get("letzter_erfolg")})
            return out

    # -- Schleife ----------------------------------------------------------------
    def _backup_due(self) -> bool:
        local = datetime.now(ZoneInfo(self.s.timezone))
        if local.hour < self.s.docs_backup_hour:
            return False
        last = self.state.get_job("backup").get("letzter_erfolg")
        return not last or datetime.fromisoformat(last).astimezone(ZoneInfo(self.s.timezone)).date() < local.date()

    async def loop(self) -> None:
        await asyncio.sleep(20)
        last_cal = 0.0
        while True:
            try:
                if self.google.is_connected():
                    now = asyncio.get_running_loop().time()
                    if self.s.calendar_sync and now - last_cal >= self.s.sync_interval_s:
                        last_cal = now
                        await self.run_calendar()
                    if self.s.docs_backup and self._backup_due():
                        # nach einem Fehler hoechstens alle 30 Minuten erneut versuchen
                        prev = self.state.get_job("backup").get("zuletzt")
                        if not prev or (datetime.now(timezone.utc) - datetime.fromisoformat(prev)).total_seconds() > 1800:
                            await self.run_backup()
            except Exception:  # noqa: BLE001
                log.exception("Hintergrund-Schleife")
            await asyncio.sleep(30)
