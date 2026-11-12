"""Der echte Chats-Dienst (TestClient) gegen einen Beeper-MCP-Ersatz."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

CHATS = """# Chats

Found 2+ chats (showing 2)
Next page (older): cursor='abc', direction='before'

## Luka (chatID: 1)
Chat on snapchat with Luka, me.
**Type**: single
**Last Activity**: 2026-11-12T10:38:01.465Z

## Maurice (chatID: 2)
Chat on Beeper (Matrix) (matrix) with me. It has 4 unread messages.
**Type**: group
**Last Activity**: 2026-11-12T10:35:40.548Z
This chat is muted.
"""
MSGS = {"items": [
    {"id": "9", "timestamp": "2026-11-12T10:40:00Z", "senderName": "Luka", "type": "VOICE", "text": "", "isSender": False, "linkedMessageID": "8",
     "attachments": [{"srcURL": "mxc://x/voice", "type": "audio", "mimeType": "audio/ogg", "isVoiceNote": True, "duration": 12}],
     "reactions": [{"reactionKey": "👍", "isSender": True}, {"reactionKey": "👍"}]},
    {"id": "8", "timestamp": "2026-11-12T10:38:00Z", "senderName": "me", "type": "TEXT", "text": "Hi", "isSender": True,
     "attachments": [{"srcURL": "mxc://x/img", "type": "img", "mimeType": "image/jpeg"}]}], "hasMore": True, "oldestCursor": "8", "newestCursor": "9"}
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAH5H4tQAAAABJRU5ErkJggg=="


class FakeBeeper:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.chats = CHATS
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if self.headers.get("authorization") != "Bearer tok":
                    self.send_response(401); self.end_headers(); return
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                m, rid = body["method"], body.get("id")
                res: dict = {}
                if m == "initialize":
                    res = {"protocolVersion": "2025-03-26", "capabilities": {}}
                elif m == "tools/call":
                    n, a = body["params"]["name"], body["params"]["arguments"]
                    outer.calls.append((n, a))
                    if n == "search_chats":
                        res = {"content": [{"type": "text", "text": outer.chats}]}
                    elif n == "list_messages":
                        res = {"content": [{"type": "text", "text": json.dumps(MSGS)}]}
                    elif n == "get_attachment_image":
                        res = {"content": [{"type": "image", "data": PNG, "mimeType": "image/png"}]}
                    elif n == "transcribe_voice_message":
                        res = {"content": [{"type": "text", "text": "Hallo Welt"}]}
                    else:
                        res = {"content": [{"type": "text", "text": "Sent message 99"}]}
                if rid is None:
                    self.send_response(202); self.end_headers(); return
                out = json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}).encode()
                self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("mcp-session-id", "s1")
                self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/mcp"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def last(self, name):
        return next(a for n, a in reversed(self.calls) if n == name)


@pytest.fixture(scope="session")
def fake():
    return FakeBeeper()


@pytest.fixture(scope="session")
def client(fake):
    os.environ.update({"BEEPER_MCP_URL": fake.url, "BEEPER_MCP_TOKEN": "tok", "LIVE_POLL_S": "0.2",
                       "FRONTEND_DIR": os.path.join(os.path.dirname(__file__), "..", "..", "frontend")})
    from fastapi.testclient import TestClient

    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c
