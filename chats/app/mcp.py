"""Kleiner MCP-Client (Streamable HTTP) für Beeper Desktop: eine Sitzung wird wiederverwendet, bei Fehlern neu aufgebaut."""
import asyncio
import json

import httpx

PROTOCOL = "2025-03-26"


class BeeperError(Exception):
    def __init__(self, msg: str, status: int = 502):
        super().__init__(msg)
        self.status = status


class Beeper:
    def __init__(self, url: str, client: httpx.AsyncClient | None = None):
        self.url = url
        self.client = client or httpx.AsyncClient(timeout=30)
        self.sid: str | None = None
        self.n = 0
        self.lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()

    async def _post(self, method: str, params: dict | None = None, notify: bool = False):
        body: dict = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            self.n += 1
            body["id"] = self.n
        headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        if self.sid:
            headers["mcp-session-id"] = self.sid
        try:
            r = await self.client.post(self.url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise BeeperError("Beeper nicht erreichbar") from e
        if r.status_code in (401, 403):
            raise BeeperError("Beeper lehnt den Zugang ab (Token prüfen)", 401)
        if r.status_code >= 400:
            raise BeeperError(f"Beeper: HTTP {r.status_code}")
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
            raise BeeperError("Beeper: " + str((msg or {}).get("error", {}).get("message") or "keine Antwort")[:200])
        return msg.get("result")

    async def _init(self) -> None:
        self.sid = None
        await self._post("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "ida-chats", "version": "1"}})
        await self._post("notifications/initialized", notify=True)

    async def content(self, tool: str, args: dict | None = None) -> list[dict]:
        args = {k: v for k, v in (args or {}).items() if v not in (None, "")}
        if not self.url:
            raise BeeperError("Beeper ist nicht eingerichtet (BEEPER_MCP_URL fehlt).", 409)
        async with self.lock:
            for attempt in (0, 1):
                try:
                    if not self.sid:
                        await self._init()
                    res = await self._post("tools/call", {"name": tool, "arguments": args})
                    break
                except BeeperError as e:
                    self.sid = None
                    if attempt or e.status == 401:
                        raise
        if res.get("isError"):
            raise BeeperError(("Beeper: " + "\n".join(c.get("text", "") for c in res.get("content", [])))[:300])
        return res.get("content", [])

    async def text(self, tool: str, args: dict | None = None) -> str:
        return "\n".join(c.get("text", "") for c in await self.content(tool, args) if c.get("type") == "text")
