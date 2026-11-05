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
    files_root: str = "/hostfs"       # / des Servers mit Schreibrecht (Tool „Dateien“)
    files_fallback: str = "/opt"      # fehlt files_root: nur dieser Ordner, unter demselben Pfad eingebunden
    net_ifaces: str = ""              # Netzwerkkarten fürs Internet (Komma-getrennt); leer = alle echten Karten (ohne lo, veth, br-, docker, wg, tailscale …)
    sample_seconds: int = 15         # so oft wird gemessen
    raw_keep_hours: int = 48          # so lange bleiben die Einzelwerte, danach nur noch 10-Minuten-Mittel
    keep_days: int = 365              # so lange bleiben die 10-Minuten-Mittel
    timezone: str = "Europe/Berlin"
    dashboard_public_url: str = ""
    ida_public_url: str = ""          # Link zurück zu IDA; leer = kein Link
    # Cloudflare-API (nur lesend), derselbe Token wie beim Cloudflare-MCP im IDA-Stack: Access-Apps (Zero Trust) und Tunnel-Routen.
    # Leer = Schutz der URLs wird per Aufruf ohne Login geprüft, Routen nur über den Metrics-Port von cloudflared.
    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""   # leer = das erste Konto, das der Token sieht
    cloudflare_api_url: str = "https://api.cloudflare.com/client/v4"

    # Dieselbe App läuft zweimal im IDA-Stack: "dashboard" = Überblick (Übersicht, Stacks, Dateien, Leistung, Speicher),
    # "server" = IDA Server zum Einrichten (Websites, Veröffentlichen, Stacks, Dateien). Siehe docs/SERVER.md
    app_mode: str = "dashboard"
    server_public_url: str = ""
    dashboard_url: str = ""           # nur Server: intern zum Dashboard (Messwerte für die Stacks), z. B. http://dashboard:8000
    sites_dir: str = "/opt/ida-sites"  # nur Server: Websites (www/<name>, conf/sites.conf); muss auch im nginx-Dienst „sites“ hängen
    sites_upstream: str = ""          # nur Server: so erreicht der Tunnel den nginx der Websites, z. B. http://10.7.0.1:8087; leer = automatisch
    tunnel_host_ip: str = ""          # nur Server: IP des Servers für Tunnel-Ziele (Ports); leer = aus den vorhandenen Tunnel-Regeln

    @property
    def is_server(self) -> bool:
        return self.app_mode.strip().lower() == "server"

    @property
    def stack_dirs(self) -> list[Path]:
        return [Path(p.strip()) for p in self.stacks_dirs.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
