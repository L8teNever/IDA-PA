"""Einstellungen aus Umgebungsvariablen (siehe docs/MAIL.md)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    # Gmail-Zugang kommt vom Dienst "google" (dort ist die Google-Anmeldung, Einstellungen → Google in IDA)
    google_api_url: str = "http://google:4571"
    google_api_token: str = ""
    gmail_api_url: str = "https://gmail.googleapis.com/gmail/v1/users/me"   # nur für Tests änderbar
    gmail_upload_url: str = "https://gmail.googleapis.com/upload/gmail/v1/users/me"
    mail_public_url: str = ""
    live_poll_s: float = 10.0       # so oft fragt der Dienst Gmail nach Änderungen – nur solange die App irgendwo offen ist
    page_size: int = 30             # Konversationen pro Seite


@lru_cache
def get_settings() -> Settings:
    return Settings()
