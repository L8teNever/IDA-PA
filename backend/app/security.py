"""The web UI has no login of its own (access control sits in front of it, e.g. Cloudflare Zero Trust).

Internal routes (/mcp/{token}) authenticate with their own per-run token.
"""


async def websocket_allowed(ws) -> bool:
    """WebSockets: the page must come from this host (no cross-site use of the terminal)."""
    origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
    return not origin or origin.split("://", 1)[-1] == host
