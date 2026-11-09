"""Einstellungen aus Umgebungsvariablen (siehe .env.example und docs/DOCS.md)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")        # Volume /data: docs/<id>.idadocs und docs/<id>.md
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    ida_internal_url: str = "http://app:8000"   # Chat mit Ida im selben Stack

    @property
    def docs_dir(self) -> Path:
        return self.data_dir / "docs"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.docs_dir.mkdir(parents=True, exist_ok=True)
    return s
