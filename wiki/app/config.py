"""Einstellungen aus Umgebungsvariablen (siehe .env.example im Repo-Root)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")          # Volume /data: wiki.db, vault/, files/, backups/
    timezone: str = "Europe/Berlin"
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"

    wiki_public_url: str = ""                # z. B. https://wiki.kulbarts.com
    wiki_ida_token: str = ""                 # IDA <-> Wiki (nur im internen Docker-Netz)
    wiki_mcp_token: str = ""                 # Master-Token für externes MCP unter /mcp; leer = nur benannte Tokens oder aus
    ida_internal_url: str = ""               # für „Ida fragen“ und Direktlinks zurück in IDA
    ollama_url: str = ""                     # optional: lokale Embeddings für semantische Gedächtnissuche
    embed_model: str = ""                    # leer = automatisch erkennen

    wiki_max_file_mb: int = 50               # maximale Größe eines Dateianhangs
    scheduler_enabled: bool = True
    wiki_testing: bool = False
    backup_keep: int = 14
    trash_days: int = 30
    proposal_minutes: int = 30

    @property
    def db_path(self) -> Path:
        return (self.data_dir / "wiki.db").resolve()

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    @property
    def vault_dir(self) -> Path:
        return (self.data_dir / "vault").resolve()

    @property
    def files_dir(self) -> Path:
        return (self.data_dir / "files").resolve()

    @property
    def backup_dir(self) -> Path:
        return (self.data_dir / "backups").resolve()


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.vault_dir, s.files_dir, s.backup_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
