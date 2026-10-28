"""Einstellungen → GitHub: sign-in (Device Flow or token), status, repos. The token never leaves the server."""
from fastapi import APIRouter

from ..schemas import GithubClient, GithubToken
from ..services import code as code_area
from ..services.code import github
from .deps import DB

router = APIRouter(prefix="/api/v1/github", tags=["github"])


@router.get("/status")
async def status(db: DB):
    return await github.status(db)


@router.put("/client-id")
async def client_id(body: GithubClient, db: DB):
    await code_area.require(db)
    await github.set_client_id(db, body.client_id)
    return await github.status(db)


@router.post("/login")
async def start_login(db: DB):
    """Device Flow: returns the code the user enters on github.com/login/device; the server waits for the OK."""
    await code_area.require(db)
    return await github.start_login(db)


@router.delete("/login")
async def cancel_login():
    github.cancel_login()
    return {"ok": True}


@router.post("/token")
async def token(body: GithubToken, db: DB):
    await code_area.require(db)
    await github.connect_token(db, body.token, via="token")
    return await github.status(db)


@router.delete("")
async def logout(db: DB):
    await github.logout(db)
    return await github.status(db)


@router.get("/repos")
async def repos(db: DB, q: str = ""):
    await code_area.require(db)
    return await github.repos(db, q)
