"""Einstellungen aus Umgebungsvariablen (siehe docs/CHATS.md)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    # MCP-Adresse von Beeper Desktop inkl. Zugangs-Token (die gleiche wie im Beeper-Anbieter in IDA)
    beeper_mcp_url: str = ""
    chats_public_url: str = ""
    live_poll_s: float = 5.0        # so oft fragt der Dienst Beeper nach Änderungen – nur solange die App irgendwo offen ist
    page_size: int = 40


@lru_cache
def get_settings() -> Settings:
    return Settings()
