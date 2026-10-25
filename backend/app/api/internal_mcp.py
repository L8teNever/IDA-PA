"""Internal MCP endpoint for running agents (each run has its own secret token in the URL)."""
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from sqlalchemy import func, select

from ..db import session_scope
from ..models import Connector
from ..services import trigger_script
from ..services.mcp.client import call_tool
from ..services.mcp.hub import handle

router = APIRouter(tags=["internal"], include_in_schema=False)


@router.post("/mcp/script/{token}")
async def script_tool(token: str, request: Request):
    """Tool calls of a running trigger script (token valid only while that script runs)."""
    if not trigger_script.valid_token(token):
        return Response(status_code=404)
    body = await request.json()
    name = str(body.get("connector") or "")
    async with session_scope() as db:
        c = await db.get(Connector, name) or await db.scalar(select(Connector).where(func.lower(Connector.name) == name.lower()))
        if not c or not c.enabled:
            return JSONResponse({"error": f"MCP-Server „{name}“ gibt es nicht (oder er ist aus)"})
        if c.tools and body.get("tool") not in c.tools:
            return JSONResponse({"error": f"Tool „{body.get('tool')}“ gibt es bei {c.name} nicht"})
        try:
            return JSONResponse({"text": await call_tool(c, str(body.get("tool")), body.get("args") or {})})
        except Exception as e:  # noqa: BLE001 – hand every error to the script
            return JSONResponse({"error": str(e)[:500] or e.__class__.__name__})


@router.post("/mcp/{token}")
async def mcp(token: str, request: Request):
    reply = await handle(token, await request.json())
    if reply is False:
        return Response(status_code=404)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)
