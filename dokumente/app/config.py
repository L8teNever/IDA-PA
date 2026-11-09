"""Einstellungen aus Umgebungsvariablen (siehe .env.example und docs/DOCS.md)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")        # Volume /data: docs/<id>.idadocs und docs/<id>.md
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    # Backup nach Google Drive (alles leer = aus). OAuth-Zugang mit Scope drive.file.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    drive_folder_id: str = ""              # Zielordner in Drive; leer = Ordner „IDA Docs“ wird angelegt

    @property
    def docs_dir(self) -> Path:
        return self.data_dir / "docs"

    @property
    def drive_on(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.docs_dir.mkdir(parents=True, exist_ok=True)
    return s
