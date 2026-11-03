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

    # Kalender (eigener Dienst, siehe docs/KALENDER.md): Adresse und gemeinsames Geheimnis; leer = Kalender-Funktionen aus
    kalender_url: str = ""
    kalender_ida_token: str = ""
    kalender_public_url: str = ""  # öffentliche Adresse der Kalender-App (Seitenleiste, Termin-Links); leer = Eintrag versteckt

    # Wiki (eigener Dienst, siehe docs/WIKI.md): Adresse, Geheimnis und öffentliche URL; leer = Wiki-Funktionen aus
    wiki_url: str = ""
    wiki_ida_token: str = ""
    wiki_public_url: str = ""

    # Code-App (eigener Dienst, siehe docs/CODE.md): öffentliche URL; leer = Code-Bereich bleibt hier in IDA
    code_public_url: str = ""

    # Dashboard-App (eigener Dienst, siehe docs/DASHBOARD.md): öffentliche URL; leer = kein Eintrag in der Seitenleiste
    dashboard_public_url: str = ""

    # Stella / Live-Sprache (eigener Container „voice“ aus demselben Image, siehe docs/STELLA.md).
    # In IDA: wo der Voice-Dienst intern läuft, das gemeinsame Geheimnis und die öffentliche Adresse (Browser + Telnyx).
    voice_url: str = ""  # z. B. http://voice:8000; leer = Live-Gespräch und Anrufe aus
    voice_secret: str = ""  # gemeinsames Geheimnis IDA <-> voice (auch für die Browser-Tickets)
    voice_public_url: str = ""  # z. B. https://voice.example.com
    voice_telnyx_url: str = ""  # Adresse nur für Telnyx (Webhook/Media); leer = voice_public_url
    voice_app_url: str = ""  # Adresse der eigenständigen Live-App (z. B. https://voice.example.com); leer = voice_public_url
    voice_agent: str = "stella"  # dieser Agent spricht (Profil, Gedächtnis, Chats)
    # Im Gespräch direkt angebotene Tools (Muster, * erlaubt); alle anderen über tool_suchen/tool_ausfuehren
    voice_core_tools: str = ("calendar_day_brief,calendar_events,calendar_free_time,calendar_search,calendar_event_create,calendar_event_move,"
                             "reminder_create,reminder_list,tracker_list,tracker_check,memory_save,memory_search,wiki_search,wiki_read,"
                             "agent_delegate,stundenplan,vertretungen,ausfaelle,sofia_list_homework,google_termine_liste,google_mails_suchen,"
                             "homeassistant_zustand_lesen,homeassistant_einschalten,homeassistant_ausschalten")
    # Im Gespräch nie erlaubt (Muster gegen den Tool-Namen)
    voice_deny_tools: str = ("ssh_*,cloudflare_*,code_*,stella_*,agent_create,agent_update,agent_delete,skill_create,skill_update,"
                             "skill_delete,routine_delete,tracker_delete,memory_delete")
    # Im Voice-Container: wie er IDA erreicht, Gemini Live und Telnyx
    voice_ida_url: str = "http://app:8000"
    gemini_api_key: str = ""
    gemini_live_model: str = "gemini-3.8-live"
    gemini_live_fallbacks: str = "gemini-3.1-flash-live-preview,gemini-2.5-flash-native-audio-preview-12-2025"
    gemini_voice: str = "Aoede"
    gemini_thinking_budget: int = 0  # -1 = nicht senden
    gemini_live_url: str = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    telnyx_api_key: str = ""
    telnyx_connection_id: str = ""
    telnyx_from_number: str = ""
    telnyx_public_key: str = ""  # Ed25519 (Mission Control) – prüft die Webhooks
    telnyx_api_base: str = "https://api.telnyx.com/v2"
    stella_owner_number: str = ""  # deine Nummer: nur sie darf anrufen, nur mit ihr hat Stella Zugriff
    voice_max_call_min: int = 30
    # Telefon: eigene Sprach-Erkennung gegen Auto- und Motorgeräusche (aus dem alten Stella übernommen)
    voice_phone_vad: bool = True
    voice_vad_rms: int = 500
    voice_vad_silence_ms: int = 120
    voice_vad_min_speech_ms: int = 60
    voice_greeting_barge_in_s: float = 2.5
    voice_echo_rms_factor: float = 3.0

    @property
    def model_list(self) -> list[str]:
        return [m.strip() for m in self.models.split(",") if m.strip()]

    @property
    def voice_on(self) -> bool:
        return bool(self.voice_url and self.voice_secret)

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
