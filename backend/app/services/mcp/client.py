"""Minimal MCP client (streamable HTTP) for the saved connectors: list tools, call one tool."""
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from ...models import Connector

PROTOCOL = "2025-03-26"


class McpError(Exception):
    pass


class _Session:
    def __init__(self, connector: Connector, client: httpx.AsyncClient):
        self.c, self.client, self.sid, self.next_id = connector, client, None, 1

    async def call(self, method: str, params: dict | None = None, notify: bool = False):
        body = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            body["id"] = self.next_id
            self.next_id += 1
        headers = {"content-type": "application/json", "accept": "application/json, text/event-stream", **(self.c.headers or {})}
        if self.sid:
            headers["mcp-session-id"] = self.sid
        r = await self.client.post(self.c.url, json=body, headers=headers)
        if r.status_code >= 400:
            raise McpError(f"HTTP {r.status_code}")
        self.sid = r.headers.get("mcp-session-id") or self.sid
        if notify:
            return None
        msg = None
        if "text/event-stream" in r.headers.get("content-type", ""):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        m = json.loads(line[5:])
                    except ValueError:
                        continue
                    if m.get("id") == body["id"]:
                        msg = m
        else:
            msg = r.json()
        if not msg or msg.get("error"):
            raise McpError((msg or {}).get("error", {}).get("message") or "keine Antwort")
        return msg.get("result")


async def _open(connector: Connector, client: httpx.AsyncClient) -> _Session:
    s = _Session(connector, client)
    await s.call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "ida", "version": "1"}})
    await s.call("notifications/initialized", notify=True)
    return s


async def list_tools(connector: Connector) -> list[str]:
    async with httpx.AsyncClient(timeout=20) as client:
        s = await _open(connector, client)
        names, cursor = [], None
        for _ in range(20):
            res = await s.call("tools/list", {"cursor": cursor} if cursor else {})
            names += [t["name"] for t in res.get("tools", [])]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return names


async def call_tool(connector: Connector, tool: str, args: dict | None = None) -> str:
    """Calls one tool and returns its text output (raises McpError on tool errors)."""
    async with httpx.AsyncClient(timeout=30) as client:
        s = await _open(connector, client)
        res = await s.call("tools/call", {"name": tool, "arguments": args or {}})
    text = "\n".join(c.get("text", "") for c in res.get("content", []))
    if res.get("isError"):
        raise McpError(("Tool-Fehler: " + text)[:300])
    return text


SECRET_KEY_RE = re.compile(r"token|key|auth|secret|sig|pass|bearer", re.I)


def mask_url(href: str) -> tuple[str, bool]:
    """URL for display: secrets in the query, user:pass@ and long opaque path segments are hidden."""
    u = urlsplit(href)
    secret = False
    netloc = u.netloc
    if "@" in netloc:
        netloc, secret = "***@" + netloc.split("@", 1)[1], True
    q = []
    for k, v in parse_qsl(u.query, keep_blank_values=True):
        if SECRET_KEY_RE.search(k) and v:
            v, secret = "***", True
        q.append((k, v))
    segs = []
    for seg in u.path.split("/"):
        if re.fullmatch(r"[\w-]{24,}", seg) and re.search(r"\d", seg) and re.search(r"[a-zA-Z]", seg):
            seg, secret = "***", True
        segs.append(seg)
    return urlunsplit((u.scheme, netloc, "/".join(segs), urlencode(q, safe="*"), "")), secret
