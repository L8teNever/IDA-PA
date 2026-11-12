"""Einstellungen aus Umgebungsvariablen (siehe docs/KORREKTUR.md)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    korrektur_model: str = "oliverguhr/spelling-correction-german-base"   # Hugging-Face-Modell (T5), lädt beim ersten Start nach /data/hf
    korrektur_threads: int = 4      # Prozessorkerne fürs Modell
    korrektur_max_chars: int = 1500  # längere Texte werden nicht korrigiert
    korrektur_chunk_words: int = 25  # so viele Wörter bekommt das Modell auf einmal (längere Stücke verwirren es)


@lru_cache
def get_settings() -> Settings:
    return Settings()
