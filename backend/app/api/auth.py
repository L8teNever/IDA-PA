from fastapi import APIRouter, HTTPException, Request, Response

from ..config import get_settings
from ..schemas import Login
from ..security import COOKIE, make_cookie, valid_access, valid_cookie, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login")
async def login(body: Login, response: Response):
    if not verify_password(body.password):
        raise HTTPException(401, "Falsches Passwort")
    s = get_settings()
    response.set_cookie(COOKIE, make_cookie(), max_age=s.session_days * 86400, httponly=True, samesite="lax", secure=s.cookie_secure)
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    via_access = await valid_access(request)
    return {"logged_in": via_access or valid_cookie(request.cookies.get(COOKIE)), "via": "cloudflare" if via_access else "password"}
