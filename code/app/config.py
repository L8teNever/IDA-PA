"""Einstellungen aus Umgebungsvariablen (siehe docker-compose.yml)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    ida_internal_url: str = "http://app:8000"   # IDA im Docker-Netz: dort laufen KI, Projekte, Git und GitHub
    code_public_url: str = ""                    # z. B. https://code.kulbarts.com
    ida_public_url: str = ""                     # Link zurück zu IDA (Einstellungen); leer = kein Link


@lru_cache
def get_settings() -> Settings:
    return Settings()
