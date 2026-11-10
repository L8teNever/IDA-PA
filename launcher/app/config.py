"""Einstellungen aus Umgebungsvariablen (siehe docker-compose.yml)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    data_dir: Path = Path("/data")                  # icons/ (Zwischenspeicher der Seiten-Symbole)
    # Cloudflare-API (nur lesend): derselbe Token wie beim Cloudflare-MCP. Braucht Access: Apps and Policies Read, Tunnel Read.
    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""                 # leer = das erste Konto, das der Token sieht
    cloudflare_api_url: str = "https://api.cloudflare.com/client/v4"
    cache_seconds: int = 60                         # so lange gilt die Liste, bevor sie neu von Cloudflare geholt wird
    hide: str = ""                                  # Adressen, die nicht erscheinen sollen (Komma-getrennt, * erlaubt), z. B. "adminkira.*,*.l8tenever.com"
    extra: str = ""                                 # zusätzliche Einträge: "Name=https://adresse, Name2=https://adresse2"
    show_unprotected: bool = True                   # auch Tunnel-Adressen ohne Access-App zeigen (mit Hinweis „offen“)
    title: str = "Willkommen zurück!"


@lru_cache
def get_settings() -> Settings:
    return Settings()
