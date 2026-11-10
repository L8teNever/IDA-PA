"""Einstellungen → Google: Anmelden/Abmelden und Stand. Die Google-Anmeldung selbst macht der Dienst `google` (siehe docs/GOOGLE.md);
IDA reicht nur durch und bekommt dafür einen einmaligen Anmelde-Link. Der Zugang bleibt im Dienst, weder Browser noch KI sehen ihn."""
from urllib.parse import quote

import httpx
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from ..config import get_settings
from ..services.errors import Invalid

router = APIRouter(prefix="/api/v1/google", tags=["google"])


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
async def login():
    """Gibt die Adresse von Googles Zustimmungsseite zurück. Google kommt danach zu /api/v1/google/callback in IDA zurück."""
    return await _call("POST", "/auth/start")


@router.get("/callback", include_in_schema=False)
async def callback(code: str = "", state: str = "", error: str = ""):
    """Rückweg von Google: code und state gehen an den Google-Dienst, danach zurück in die Einstellungen."""
    if error or not code:
        return RedirectResponse(f"/einstellungen/google?google=fehler&grund={quote(error or 'abgebrochen')}", status_code=303)
    try:
        await _call("POST", "/auth/callback", json={"code": code, "state": state})
    except Invalid as e:
        return RedirectResponse(f"/einstellungen/google?google=fehler&grund={quote(str(e))}", status_code=303)
    return RedirectResponse("/einstellungen/google?google=ok", status_code=303)


@router.delete("")
async def logout():
    await _call("POST", "/auth/disconnect")
    return await status()
