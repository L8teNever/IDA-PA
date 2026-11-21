"""Einstellungen: Standardwerte, darüber Umgebungsvariablen (.env), darüber das, was im Browser unter „Einstellungen“ gespeichert ist
(Tabelle settings in health.db, siehe settings_ui.py). Alles außer Port und Datenordner lässt sich im Browser ändern und gilt sofort."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    data_dir: Path = Path("/data")                  # health.db
    public_url: str = ""                            # HEALTH_PUBLIC_URL, z. B. https://health.kulbarts.com (Install-Befehl, Koppeln)
    # Zugriff auf Oberfläche und API:
    #   IDA (intern) mit Bearer HEALTH_IDA_TOKEN; der Browser über Cloudflare Access. Mit ACCESS_TEAM + ACCESS_AUD prüft Health das
    #   Access-JWT selbst (empfohlen – dann hilft auch ein direkter Aufruf am Port nicht). Ohne beides: zu (503) – außer ALLOW_OPEN=1 (nur zum Testen).
    ida_token: str = ""
    access_team: str = ""                           # HEALTH_ACCESS_TEAM: z. B. kulbarts  (-> kulbarts.cloudflareaccess.com)
    access_aud: str = ""                            # HEALTH_ACCESS_AUD: „Application Audience (AUD) Tag“ der Access-App
    allow_open: bool = False                        # ALLOW_OPEN=1: ohne Token/Access trotzdem offen – NUR zum Testen
    # Messwerte
    store_seconds: int = 10                         # Einzelwerte höchstens so oft speichern (live kommen sie öfter)
    raw_keep_hours: int = 48
    keep_days: int = 400
    live_interval: int = 2                          # so oft schicken Geräte, solange jemand zuschaut
    idle_interval: int = 10
    # Cloudflare Mesh: Health legt beim Koppeln einen Mesh-Knoten an (Token braucht „Cloudflare One Connectors Write“)
    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""
    cloudflare_api_url: str = "https://api.cloudflare.com/client/v4"
    # Health selbst im Mesh (WARP im Container, siehe selfmesh.py): Adresse für die Geräte, leer = automatisch die eigene Mesh-IP
    mesh_address: str = ""
    mesh_node: str = ""                             # Mesh-Knoten von Health (setzt „Health ins Mesh aufnehmen“)
    mesh_ip: str = ""                               # zuletzt gesehene Mesh-IP von Health (bleibt, auch wenn WARP kurz weg ist)
    install_url: str = "https://raw.githubusercontent.com/L8teNever/Ida-connect/main/install.sh"
    # IDA: „Mit IDA verbinden“ trägt dort Adresse und Token von Health ein (interne Adressen im Docker-Netz)
    ida_url: str = "http://app:8000"
    internal_url: str = "http://health:8000"


_overrides: dict = {}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for k, v in _overrides.items():
        setattr(s, k, v)
    return s


def set_overrides(values: dict) -> None:
    """Im Browser gespeicherte Werte übernehmen (gilt sofort für alle, die get_settings() aufrufen)."""
    _overrides.clear()
    _overrides.update(values)
    get_settings.cache_clear()


def env_settings() -> Settings:
    """Nur Standard + Umgebung (für „aus .env“ und Zurücksetzen)."""
    return Settings()
