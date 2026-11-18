"""Einstellungen aus Umgebungsvariablen (siehe docs/WHISPER.md)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    whisper_model: str = "large-v3-turbo"   # faster-whisper-Name oder Hugging-Face-Modell; lädt beim ersten Start nach /data/hf
    whisper_threads: int = 4                # Prozessorkerne fürs Modell
    whisper_compute: str = "int8"           # int8 = schnell und sparsam auf dem Prozessor
    whisper_beam: int = 1                   # 1 = schnell (greedy); höher = etwas genauer, aber langsamer
    whisper_max_mb: int = 25                # größere Dateien werden abgelehnt
    whisper_language: str = ""              # leer = Sprache pro Nachricht erkennen; sonst z. B. "de"


@lru_cache
def get_settings() -> Settings:
    return Settings()
