"""Einstellungen aus Umgebungsvariablen (siehe .env.example im Repo-Root)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")          # Volume /data: kalender.db, backups/, files/
    timezone: str = "Europe/Berlin"
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"

    kalender_public_url: str = ""
    kalender_ida_token: str = ""             # IDA <-> Kalender (nur im internen Docker-Netz)
    kalender_mcp_token: str = ""             # externes MCP unter /mcp; leer = aus
    kalender_state: str = "NW"               # Bundesland für Feiertage
    ida_webhook_url: str = ""                # hierhin gehen fällige Erinnerungen; leer = Kalender läuft allein
    ida_internal_url: str = ""               # nur für „Ida fragen“

    scheduler_enabled: bool = True
    kalender_testing: bool = False
    backup_keep: int = 14
    trash_days: int = 30
    changes_days: int = 90

    @property
    def db_path(self) -> Path:
        return (self.data_dir / "kalender.db").resolve()

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.backup_dir, s.files_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
