"""Konfiguration des Ida-Google MCP Servers, komplett über Umgebungsvariablen."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Umgebungsvariable {name} fehlt oder ist leer.")
    return value


def _optional(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "ja", "an", "on")


def _require_min_length(name: str, min_length: int) -> str:
    value = _require(name)
    if len(value) < min_length:
        raise ConfigError(
            f"{name} ist zu kurz (mind. {min_length} Zeichen). "
            "Erzeuge z.B. mit: openssl rand -hex 32"
        )
    return value


@dataclass(frozen=True)
class Settings:
    # OAuth-Client, in der Google Cloud Console angelegt (Typ "Web
    # Application"). google_redirect_uri muss dort 1:1 als "Autorisierte
    # Weiterleitungs-URI" eingetragen sein.
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str

    # MCP-Port: die eigentlichen Google-Werkzeuge, per Bearer-Token
    # abgesichert wie bei Ida-Untis/Ida-Telegram/Ida-Memory.
    mcp_auth_token: str
    mcp_host: str
    mcp_port: int

    # Auth-Port: nur fuer den einmaligen (oder bei neuen Scopes erneuten)
    # Google-Anmelde-Flow. Gedacht, um zusaetzlich hinter Cloudflare Zero
    # Trust Access zu liegen -- auth_token ist nur ein zusaetzliches,
    # kostenloses Sicherheitsnetz, falls das mal nicht greift.
    auth_host: str
    auth_port: int
    auth_token: str

    # Wo der Google-Refresh-Token dauerhaft gespeichert wird (Docker-Volume).
    token_file_path: str

    # --- IDA-Stack: interne API und Abgleich-Aufgaben (alles optional) ---
    data_dir: str = "/data"
    public_url: str = ""                 # oeffentliche Adresse der Anmeldeseite (z. B. https://google.kulbarts.com)
    return_bases: tuple = ()             # Adressen, zu denen es nach der Anmeldung zurueckgehen darf (z. B. https://ida.kulbarts.com)
    # Interne REST-API (nur im Docker-Netz, wird NICHT auf den Host veroeffentlicht). api_token leer = ohne Token.
    api_host: str = "0.0.0.0"
    api_port: int = 4571
    api_token: str = ""
    timezone: str = "Europe/Berlin"
    # Kalender-Abgleich: IDA-Kalender <-> Google-Kalender (nur aktiv, wenn calendar_sync=True)
    calendar_sync: bool = False
    kalender_url: str = "http://kalender:8000"
    kalender_token: str = ""
    kalender_calendar: str = ""          # Name oder ID des IDA-Kalenders; leer = der Standardkalender
    google_calendar_id: str = "primary"
    sync_interval_s: int = 300
    sync_past_days: int = 30
    sync_future_days: int = 365
    sync_max_deletes: int = 20
    # Docs-Backup nach Google Drive (nur aktiv, wenn docs_backup=True)
    docs_backup: bool = False
    docs_url: str = "http://docs:8000"
    docs_backup_hour: int = 3
    docs_backup_keep: int = 14
    docs_backup_folder: str = "IDA Docs Backup"


def load_settings() -> Settings:
    try:
        mcp_auth_token = _require_min_length("MCP_AUTH_TOKEN", 16)
        auth_token = _require_min_length("AUTH_TOKEN", 16)

        settings = Settings(
            google_client_id=_require("GOOGLE_CLIENT_ID"),
            google_client_secret=_require("GOOGLE_CLIENT_SECRET"),
            google_redirect_uri=_require("GOOGLE_REDIRECT_URI"),
            mcp_auth_token=mcp_auth_token,
            mcp_host=_optional("MCP_HOST", "0.0.0.0"),
            mcp_port=int(_optional("MCP_PORT", "4569")),
            auth_host=_optional("AUTH_HOST", "0.0.0.0"),
            auth_port=int(_optional("AUTH_PORT", "4570")),
            auth_token=auth_token,
            token_file_path=_optional("GOOGLE_TOKEN_FILE_PATH", "/data/google_token.json"),
            data_dir=_optional("DATA_DIR", "/data"),
            public_url=_optional("GOOGLE_PUBLIC_URL", "").rstrip("/"),
            return_bases=tuple(b.strip().rstrip("/") for b in _optional("GOOGLE_RETURN_BASES", "").split(",") if b.strip()),
            api_host=_optional("API_HOST", "0.0.0.0"),
            api_port=int(_optional("API_PORT", "4571")),
            api_token=_optional("GOOGLE_API_TOKEN", ""),
            timezone=_optional("TIMEZONE", "Europe/Berlin"),
            calendar_sync=_flag("CALENDAR_SYNC"),
            kalender_url=_optional("KALENDER_URL", "http://kalender:8000").rstrip("/"),
            kalender_token=_optional("KALENDER_IDA_TOKEN", ""),
            kalender_calendar=_optional("CALENDAR_SYNC_KALENDER", ""),
            google_calendar_id=_optional("CALENDAR_SYNC_GOOGLE", "primary"),
            sync_interval_s=max(60, int(_optional("CALENDAR_SYNC_INTERVAL", "300"))),
            sync_past_days=int(_optional("CALENDAR_SYNC_PAST_DAYS", "30")),
            sync_future_days=int(_optional("CALENDAR_SYNC_FUTURE_DAYS", "365")),
            sync_max_deletes=int(_optional("CALENDAR_SYNC_MAX_DELETES", "20")),
            docs_backup=_flag("DOCS_BACKUP"),
            docs_url=_optional("DOCS_URL", "http://docs:8000").rstrip("/"),
            docs_backup_hour=int(_optional("DOCS_BACKUP_HOUR", "3")),
            docs_backup_keep=max(1, int(_optional("DOCS_BACKUP_KEEP", "14"))),
            docs_backup_folder=_optional("DOCS_BACKUP_FOLDER", "IDA Docs Backup"),
        )
    except ConfigError as exc:
        print(f"[Ida-Google] Konfigurationsfehler: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    return settings
