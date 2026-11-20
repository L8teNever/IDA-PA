"""Einstellungen aus Umgebungsvariablen (siehe docs/PIPER.md)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    piper_dir: str = "/data/voices"         # hier landen die geladenen Stimmen (bleiben im Volume)
    piper_voices_url: str = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    piper_default_voice: str = "de_DE-thorsten-high"
    piper_preload: bool = True              # Standardstimme beim Start laden (erste Antwort ohne Wartezeit)
    piper_keep: int = 2                     # so viele Stimmen bleiben gleichzeitig im Speicher
    piper_max_chars: int = 2000             # längere Texte werden abgelehnt


@lru_cache
def get_settings() -> Settings:
    return Settings()
