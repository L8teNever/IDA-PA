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

    # Claude Code CLI
    claude_bin: str = "claude"
    agy_runtime_dir: str = ""  # Antigravity ACP runtime; empty = downloaded into data/agy-runtime/<version>
    models: str = "claude-sonnet-5-5,claude-opus-5-5,claude-haiku-5-5,claude-fable-5-1"
    default_model: str = "claude-sonnet-5-5"
    cheap_model: str = "claude-haiku-5-5"
    timezone: str = "Europe/Berlin"  # routines with fixed times run in this zone  # memory consolidation and other housekeeping
    run_timeout_s: int = 600

    # Speech input (microphone button): local Whisper via faster-whisper, model downloaded into data/whisper
    whisper_model: str = "small"  # tiny | base | small | medium | large-v3 (bigger = better, slower)
    whisper_compute_type: str = "int8"
    whisper_language: str = "de"  # empty = detect
    whisper_preload: bool = True  # load the model right after start

    # Optional embeddings for the memory (Ollama on the host)
    ollama_url: str = "http://host.docker.internal:11434"
    embed_model: str = ""  # empty = auto-detect

    # Internal URL under which spawned CLI processes reach this server's /mcp endpoint.
    internal_url: str = "http://127.0.0.1:8000"
    # Built frontend (index.html, css, js). In the image: /app/frontend.
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    scheduler_enabled: bool = True

    # Code area (projects, threads with real file/shell tools) – switched on in Einstellungen → Code
    git_bin: str = "git"
    # GitHub sign-in (Einstellungen → GitHub): Client ID of your own OAuth App with "Device Flow" enabled
    github_client_id: str = ""
    github_api_url: str = "https://api.github.com"
    github_url: str = "https://github.com"

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


    @property
    def projects_dir(self) -> Path:
        """One folder per code project (git clone / git init)."""
        return self.data_dir / "projects"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.claude_dir, s.sandbox_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
