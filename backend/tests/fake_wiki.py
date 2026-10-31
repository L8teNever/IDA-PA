"""Ersatz für den Wiki-Dienst: zeichnet Anfragen auf und liefert feste Antworten."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class FakeWiki:
    def __init__(self):
        self.requests: list[dict] = []
        self.mode = "ok"  # ok | drop | error
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _handle(self):
                n = int(self.headers.get("content-length", 0) or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = None
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                outer.requests.append({
                    "method": self.command, "path": u.path, "query": q,
                    "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body,
                })
                if outer.mode == "drop":
                    self.close_connection = True
                    return
                if outer.mode == "error":
                    return self._json({"detail": "kaputt"}, 500)
                p = u.path.replace("/api/v1", "", 1)
                if p == "/search":
                    return self._json({"results": [{"path": "memory/kern/ueber-mich", "title": "Über mich", "snippet": "Fake"}]})
                if p == "/page":
                    path = q.get("path", "memory/_index")
                    return self._json({"path": path, "title": path.split("/")[-1], "body": "# Fake\n\nInhalt.", "links": [], "backlinks": []})
                if p == "/tree":
                    return self._json({"spaces": ["privat", "memory", "ki"], "folders": [], "pages": []})
                if p == "/pages" and self.command == "POST":
                    b = body or {}
                    return self._json({"page": {"id": "pg1", "path": b.get("path"), "title": b.get("title") or "Seite", "revision": 1}})
                if p == "/pages/patch" and self.command == "POST":
                    b = body or {}
                    return self._json({"page": {"id": "pg1", "path": b.get("ref"), "title": "Seite", "revision": 2}})
                if p == "/memory/context":
                    return self._json({"text": "### Über mich\nFake-Kontext aus dem Wiki.", "core_count": 1, "matched_count": 0})
                if p == "/internal/proposals" and self.command == "POST":
                    return self._json({
                        "proposal": {"id": "pr1", "action": (body or {}).get("action"), "target_path": ((body or {}).get("payload") or {}).get("path"),
                                     "summary": "Fake-Proposal", "diff_preview": "--- a\n+++ b\n+neu\n", "status": "pending"},
                        "commit_secret": "secret123",
                    })
                if p.startswith("/internal/proposals/") and p.endswith("/commit"):
                    return self._json({"page": {"id": "pg2", "path": "privat/00-inbox/x", "title": "x", "revision": 1}})
                if p.startswith("/internal/proposals/") and p.endswith("/reject"):
                    return self._json({"ok": True})
                if p == "/internal/sync/state":
                    return self._json({"agents": {}, "projects": {}, "chats": {}})
                if p.startswith("/internal/sync/"):
                    return self._json({"ok": True, "page": {"id": "pg9", "path": "ki/ida/_agent"}})
                return self._json({"detail": "unbekannt"}, 404)

            def _json(self, data, status=200):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def last(self, path_part: str = "") -> dict:
        return [r for r in self.requests if path_part in r["path"]][-1]

    def clear(self):
        self.requests.clear()
