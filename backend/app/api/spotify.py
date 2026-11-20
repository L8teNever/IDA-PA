"""Spotify: einmal im Browser anmelden (/api/v1/spotify/login), danach steuern Ida, Stella und die Lautsprecher die Musik."""
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import get_settings
from ..services import spotify
from ..services.errors import Invalid

router = APIRouter(prefix="/api/v1/spotify", tags=["spotify"])


def _base(request: Request) -> str:
    return (get_settings().ida_public_url or str(request.base_url)).rstrip("/")


def _page(text: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                        f"<body style='font:16px system-ui;padding:24px'><h2>Spotify</h2><p>{text}</p><p><a href='/'>Zurück zu IDA</a></p>")


@router.get("/status")
async def status():
    return {"configured": spotify.configured(), "logged_in": await spotify.logged_in() if spotify.configured() else False}


@router.get("/login", include_in_schema=False)
async def login(request: Request):
    """Im Browser öffnen: weiter zu Spotifys Anmeldeseite, zurück über /callback."""
    try:
        return RedirectResponse(spotify.login_url(_base(request)), status_code=303)
    except Invalid as e:
        return _page(str(e))


@router.get("/callback", include_in_schema=False)
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return _page(f"Anmeldung abgebrochen ({quote(error or 'kein Code')}).")
    try:
        await spotify.finish_login(code, state, _base(request))
    except Invalid as e:
        return _page(str(e))
    return _page("Angemeldet. Ida, Stella und deine Lautsprecher können jetzt Musik abspielen.")


@router.delete("")
async def logout():
    await spotify.logout()
    return await status()
