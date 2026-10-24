"""App factory: API routers, login, background services and the single-page frontend."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, seed
from .api import accounts, agents, auth, chats, connectors, internal_mcp, memory, messages, routines, skills, system
from .config import get_settings
from .security import AuthMiddleware, init_auth
from .services import jobs, scheduler
from .services.errors import Invalid, NotFound

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ida")
BACKEND = Path(__file__).resolve().parents[1]


class Static(StaticFiles):
    """Static files that the browser re-validates on every load (ETag -> cheap 304), so an update is visible at once."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = "no-cache"
        return resp


def migrate() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(migrate)  # alembic runs its own event loop
    await init_auth()
    await seed.seed()
    jobs.start()
    if get_settings().scheduler_enabled:
        scheduler.start()
    log.info("IDA bereit")
    yield
    await scheduler.stop()
    await jobs.stop()
    await db.dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="IDA", version="2.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(AuthMiddleware)

    @app.exception_handler(Invalid)
    async def invalid(_: Request, e: Invalid):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(NotFound)
    async def not_found(_: Request, e: NotFound):
        return JSONResponse({"detail": str(e)}, status_code=404)

    for r in (auth, system, agents, chats, messages, routines, connectors, skills, memory, accounts, internal_mcp):
        app.include_router(r.router)

    front = s.frontend_dir
    app.mount("/static", Static(directory=front), name="static")
    index = front / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        """Every page URL (/, /chats, /einstellungen/agents/lea …) gets the app; the browser router shows the page."""
        if path.startswith(("api/", "mcp/")):
            return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)
        return FileResponse(index, headers={"cache-control": "no-cache"})

    return app


app = create_app()
