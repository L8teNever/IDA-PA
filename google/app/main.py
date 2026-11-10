"""Startet MCP-Port und interne API zusammen in einem Prozess (ein Container, zwei Ports).

uvicorn.Server().serve() ist die fuer genau diesen Fall vorgesehene
Low-Level-API (im Gegensatz zu uvicorn.run(), das genau einen Server pro
Prozess annimmt) -- asyncio.gather laesst beide ASGI-Apps nebenlaeufig im
selben Event-Loop laufen, inklusive korrektem Lifespan-Start/-Stop fuer den
MCP-Session-Manager auf dem MCP-Port.
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from app.internal_api import build_internal_app
from app.jobs.runner import Runner
from app.server import _SERVICE_MODULES, build_mcp_app, client, mcp
from app.server import google as google_auth_manager
from app.server import settings

log = logging.getLogger("ida-google")


async def _run() -> None:
    mcp_app = build_mcp_app()

    # access_log=False: uvicorn wuerde sonst jede Request-Zeile inkl. vollem
    # Pfad loggen -- und damit ein per ?token= mitgeschicktes AUTH_TOKEN/
    # MCP_AUTH_TOKEN im Klartext in die Docker-Logs schreiben.
    mcp_server = uvicorn.Server(
        uvicorn.Config(
            mcp_app,
            host=settings.mcp_host,
            port=settings.mcp_port,
            log_level="info",
            access_log=False,
        )
    )

    log.info("Google startet -- MCP %s:%s (/mcp, /healthz)", settings.mcp_host, settings.mcp_port)

    runner = Runner(settings, client, google_auth_manager)
    api_server = uvicorn.Server(
        uvicorn.Config(build_internal_app(settings, mcp, google_auth_manager, runner,
                                           {m.__name__.rsplit(".", 1)[-1]: m.SCOPES for m in _SERVICE_MODULES}), host=settings.api_host, port=settings.api_port,
                       log_level="info", access_log=False)
    )
    log.info("Interne API %s:%s | Kalender-Abgleich: %s | Docs-Backup: %s", settings.api_host, settings.api_port,
             "an" if settings.calendar_sync else "aus", "an" if settings.docs_backup else "aus")

    await asyncio.gather(mcp_server.serve(), api_server.serve(), runner.loop())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
