"""Einstellungen → Google: Anmelden/Abmelden und Stand. Die Google-Anmeldung selbst macht der Dienst `google` (siehe docs/GOOGLE.md);
IDA reicht nur durch und bekommt dafür einen einmaligen Anmelde-Link. Der Zugang bleibt im Dienst, weder Browser noch KI sehen ihn."""
import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from ..config import get_settings
from ..services.errors import Invalid

router = APIRouter(prefix="/api/v1/google", tags=["google"])


class LoginIn(BaseModel):
    return_to: str = ""


def _client() -> httpx.AsyncClient:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.google_api_token}"} if s.google_api_token else {}
    return httpx.AsyncClient(base_url=s.google_api_url.rstrip("/"), headers=headers, timeout=20)


async def _call(method: str, path: str, **kw) -> dict:
    if not get_settings().google_api_url:
        raise Invalid("Der Google-Dienst ist hier nicht eingerichtet (GOOGLE_API_URL).")
    try:
        async with _client() as c:
            r = await c.request(method, f"/api/v1{path}", **kw)
    except httpx.HTTPError as e:
        raise Invalid("Der Google-Dienst ist gerade nicht erreichbar.") from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("detail", r.text)
        except ValueError:
            msg = r.text
        raise Invalid(str(msg)[:300])
    return r.json()


@router.get("/status")
async def status():
    if not get_settings().google_api_url:
        return {"configured": False}
    return {"configured": True, **await _call("GET", "/auth/status")}


@router.post("/login")
async def login(body: LoginIn):
    """Gibt den Link zum Anmelden bei Google zurück (einmalig, 5 Minuten gültig); danach geht es zu `return_to` zurück."""
    return await _call("POST", "/auth/start", json={"return_to": body.return_to})


@router.delete("")
async def logout():
    await _call("POST", "/auth/disconnect")
    return await status()
