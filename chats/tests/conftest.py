"""Der echte Chats-Dienst (TestClient) gegen einen Beeper-MCP-Ersatz."""
import base64
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
    {"id": "9", "timestamp": "2026-11-12T10:40:00Z", "senderID": "@luka:x", "senderName": "Luka", "type": "VOICE", "text": "", "isSender": False, "linkedMessageID": "8",
     "attachments": [{"srcURL": "mxc://x/voice?encryptedFileInfoJSON=abc", "type": "audio", "mimeType": "audio/ogg", "isVoiceNote": True, "duration": 12, "transcription": {"transcription": " Hallo Mama "}}],
     "reactions": [{"reactionKey": "👍", "isSender": True}, {"reactionKey": "👍"}]},
    {"id": "8", "timestamp": "2026-11-12T10:38:00Z", "senderID": "@me:x", "senderName": "me", "type": "TEXT", "text": "Hi", "isSender": True,
     "seen": {"@me:x": "2026-11-12T10:38:01Z", "@luka:x": "2026-11-12T10:39:30Z", "@anna:x": "2026-11-12T10:39:45Z"},
     "attachments": [{"srcURL": "mxc://x/img", "type": "img", "mimeType": "image/jpeg"}]}], "hasMore": True, "oldestCursor": "8", "newestCursor": "9"}
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAH5H4tQAAAABJRU5ErkJggg=="


class FakeBeeper:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.served: list[str] = []
        self.rest: list[tuple] = []
        self.pinned: set[str] = {"2"}
        self.chats = CHATS
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):   # REST-API von Beeper Desktop (Profilbilder)
                from urllib.parse import parse_qs, urlparse
                u = urlparse(self.path)
                if self.headers.get("authorization") != "Bearer tok":
                    self.send_response(401); self.end_headers(); return
                if u.path == "/v1/chats/1/messages":
                    outer.calls.append(("rest_messages", {k: v[0] for k, v in parse_qs(u.query).items()}))
                    out, ct = json.dumps(MSGS).encode(), "application/json"
                elif u.path == "/v1/chats":
                    out, ct = json.dumps({"items": [{"id": i, "title": f"Chat {i}", "accountID": "whatsapp", "type": "single", "lastActivity": "2026-11-12T10:00:00Z",
                                                     "unreadCount": 2, "isMuted": False, "isPinned": i in outer.pinned} for i in ("1", "2", "3", "4", "5")]}).encode(), "application/json"
                elif u.path == "/v1/chats/7":
                    out = json.dumps({"id": "7", "type": "group", "imgURL": "C:\\Users\\x\\grp.png", "participants": {"items": [
                        {"id": "@luka:x", "fullName": "Luka", "imgURL": "file:///C:/Users/x/luka.png"}, {"id": "@me:x", "isSelf": True, "imgURL": None}]}}).encode()
                    ct = "application/json"
                elif u.path == "/v1/chats/1":
                    out = json.dumps({"id": "1", "type": "single", "imgURL": None, "participants": {"items": [
                        {"id": "@me:x", "isSelf": True, "imgURL": "file:///C:/Users/x/me.png"}, {"id": "@luka:x", "imgURL": "file:///C:/Users/x/luka.png"}]}}).encode()
                    ct = "application/json"
                elif u.path == "/v1/assets/serve" and (parse_qs(u.query)["url"][0] in ("file:///C:/Users/x/grp.png", "file:///C:/Users/x/luka.png")
                                                       or "/BeeperTexts/media/" in parse_qs(u.query)["url"][0]):
                    outer.served.append(parse_qs(u.query)["url"][0])
                    out, ct = base64.b64decode(PNG), "application/octet-stream"
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200); self.send_header("content-type", ct); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.rest.append(("PATCH", self.path, body))
                cid = self.path.rsplit("/", 1)[1]
                (outer.pinned.add if body.get("isPinned") else outer.pinned.discard)(cid)
                out = b'{"id": "x"}'
                self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def do_PUT(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.rest.append(("PUT", self.path, body))
                out = b'{"id": "9"}'
                self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def do_DELETE(self):
                outer.rest.append(("DELETE", self.path, None))
                out = b'{"success": true}'
                self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def do_POST(self):
                if self.path == "/v1/assets/upload":
                    raw = self.rfile.read(int(self.headers["content-length"]))
                    outer.rest.append(("UPLOAD", self.path, {"size": len(raw), "ct": self.headers["content-type"].split(";")[0], "raw": raw}))
                    out = b'{"uploadID": "up1", "fileName": "x"}'
                    self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
                    return
                if self.path.startswith("/v1/"):
                    body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                    outer.rest.append(("POST", self.path, body))
                    out = b'{"success": true, "pendingMessageID": "p7"}'
                    self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
                    return
                return self.mcp()

            def mcp(self):
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
                    elif n == "get_attachment_image" and "voice" in a["srcURL"]:
                        res = {"content": [{"type": "audio", "data": base64.b64encode(bytes(range(200))).decode(), "mimeType": "audio/ogg"}]}
                    elif n == "get_attachment_image" and "page" in a["srcURL"]:
                        res = {"content": [{"type": "resource", "data": base64.b64encode(b"<script>x</script>").decode(), "mimeType": "text/html"}]}
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
    os.environ.update({"BEEPER_MCP_URL": fake.url, "BEEPER_MCP_TOKEN": "tok", "LIVE_POLL_S": "0.2", "ARCHIVE_POLL_S": "0", "IDA_URL": "", "DATA_DIR": __import__("tempfile").mkdtemp(),
                       "FRONTEND_DIR": os.path.join(os.path.dirname(__file__), "..", "..", "frontend")})
    from fastapi.testclient import TestClient

    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c
