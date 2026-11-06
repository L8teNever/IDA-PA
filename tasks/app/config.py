"""Einstellungen aus Umgebungsvariablen (siehe .env.example im Repo-Root und docs/TASKS.md)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")          # Volume /data: tasks.db
    timezone: str = "Europe/Berlin"
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"

    tasks_public_url: str = ""
    tasks_ida_token: str = ""                # IDA <-> Tasks (nur im internen Docker-Netz)
    # Sprechen mit Stella: die App holt sich hier ein Ticket (gleiches Verfahren wie IDA) und verbindet sich direkt mit dem Voice-Dienst
    voice_secret: str = ""
    voice_public_url: str = ""

    done_keep_days: int = 60                 # so lange bleiben Erledigte in der Liste „Erledigt“

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{(self.data_dir / 'tasks.db').resolve().as_posix()}"

    @property
    def voice_on(self) -> bool:
        return bool(self.voice_secret and self.voice_public_url)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
