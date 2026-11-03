"""Einstellungen aus Umgebungsvariablen (siehe docker-compose.yml)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    data_dir: Path = Path("/data")                      # dashboard.db (Messwerte), backups/ (alte Stände der Compose-/.env-Dateien)
    docker_host: str = "unix:///var/run/docker.sock"    # oder tcp://socket-proxy:2375
    # Stack-Ordner, die zusätzlich durchsucht werden (findet auch Stacks, die gerade ganz aus sind). Komma-getrennt.
    stacks_dirs: str = "/opt/stacks"
    host_proc: str = "/host/proc"     # /proc des Servers (CPU, RAM, Netz des ganzen Servers); fehlt es, gilt /proc
    host_root: str = "/hostfs"        # / des Servers, nur lesend (Belegung der Platte); fehlt es, gilt /
    net_ifaces: str = ""              # Netzwerkkarten fürs Internet (Komma-getrennt); leer = alle echten Karten (ohne lo, veth, br-, docker, wg, tailscale …)
    sample_seconds: int = 15         # so oft wird gemessen
    raw_keep_hours: int = 48          # so lange bleiben die Einzelwerte, danach nur noch 10-Minuten-Mittel
    keep_days: int = 365              # so lange bleiben die 10-Minuten-Mittel
    timezone: str = "Europe/Berlin"
    dashboard_public_url: str = ""
    ida_public_url: str = ""          # Link zurück zu IDA; leer = kein Link

    @property
    def stack_dirs(self) -> list[Path]:
        return [Path(p.strip()) for p in self.stacks_dirs.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
