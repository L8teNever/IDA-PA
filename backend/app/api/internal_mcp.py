"""Internal MCP endpoint for running agents (each run has its own secret token in the URL)."""
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..services.mcp.hub import handle

router = APIRouter(tags=["internal"], include_in_schema=False)


@router.post("/mcp/{token}")
async def mcp(token: str, request: Request):
    reply = await handle(token, await request.json())
    if reply is False:
        return Response(status_code=404)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)
