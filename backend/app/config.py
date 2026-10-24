"""Settings from environment variables (see .env.example)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Everything that has to survive a container restart lives below data_dir (Docker volume).
    data_dir: Path = Path("./data")
    # postgresql+asyncpg://user:pass@db:5432/ida in Docker, SQLite for local development/tests.
    database_url: str = ""

    # Login for the web UI. Empty = generated at first start and printed to the log.
    app_password: str = ""
    secret_key: str = ""  # signs the session cookie; generated and stored if empty
    session_days: int = 30
    cookie_secure: bool = False  # set true behind HTTPS
    # Cloudflare Zero Trust in front of the app: requests with a valid Access token need no extra password.
    # Team domain like "myteam.cloudflareaccess.com", AUD = "Application Audience (AUD) Tag" of the Access app.
    cf_access_team_domain: str = ""
    cf_access_aud: str = ""

    # Claude Code CLI
    claude_bin: str = "claude"
    models: str = "claude-sonnet-5-5,claude-opus-5-5,claude-haiku-5-5,claude-fable-5-1"
    default_model: str = "claude-sonnet-5-5"
    cheap_model: str = "claude-haiku-5-5"  # memory consolidation and other housekeeping
    run_timeout_s: int = 600

    # Optional embeddings for the memory (Ollama on the host)
    ollama_url: str = "http://host.docker.internal:11434"
    embed_model: str = ""  # empty = auto-detect

    # Internal URL under which spawned CLI processes reach this server's /mcp endpoint.
    internal_url: str = "http://127.0.0.1:8000"
    # Built frontend (index.html, css, js). In the image: /app/frontend.
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    scheduler_enabled: bool = True

    @property
    def model_list(self) -> list[str]:
        return [m.strip() for m in self.models.split(",") if m.strip()]

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'ida.db').resolve().as_posix()}"

    @property
    def claude_dir(self) -> Path:
        """One sub folder per Claude account (its own login / CLAUDE_CONFIG_DIR)."""
        return self.data_dir / "claude"

    @property
    def sandbox_dir(self) -> Path:
        """Empty working dir for CLI runs (no project files, no git)."""
        return self.data_dir / "sandbox"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.claude_dir, s.sandbox_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
