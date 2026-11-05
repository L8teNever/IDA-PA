"""Ein kleiner Ersatz für das Dashboard: ein Stack „shop“ mit zwei Diensten, Logs, Befehle, Vorgänge (wie fake_kalender.py)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CONTAINERS = [{"id": "c-app", "name": "shop-app-1", "service": "app", "image": "ghcr.io/x/shop:latest", "state": "running", "status": "Up", "health": None},
              {"id": "c-db", "name": "shop-db-1", "service": "db", "image": "postgres:17", "state": "running", "status": "Up", "health": "healthy"}]


class FakeDashboard:
    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _handle(self):
                n = int(self.headers.get("content-length", 0) or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw else None
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                outer.requests.append({"method": self.command, "path": u.path, "query": q, "body": body})
                p = u.path
                if p == "/api/v1/stacks":
                    return self._json([{"name": "shop", "state": "running", "running": 2, "total": 2, "containers": CONTAINERS, "self": False},
                                       {"name": "ida", "state": "running", "running": 1, "total": 1, "containers": [], "self": True}])
                if p == "/api/v1/stacks/shop":
                    return self._json({"name": "shop", "state": "running", "running": 2, "total": 2, "files_ok": True, "containers": CONTAINERS,
                                       "urls": {"c-app": [{"url": "https://shop.example.com", "access": "access"}]}})
                if p.startswith("/api/v1/stacks/") and p.endswith("/action"):
                    return self._json({"job": {"id": "j1"}})
                if p == "/api/v1/jobs/j1":
                    return self._json({"id": "j1", "done": True, "ok": True, "output": ["$ docker compose pull", "→ beendet mit Code 0"]})
                if p.endswith("/logs"):
                    return self._json({"name": "shop-app-1", "text": "start\nERROR kaputt\nlaeuft"})
                if p.endswith("/exec"):
                    return self._json({"name": "shop-app-1", "exit_code": 0, "timed_out": False, "truncated": False, "output": "ausgabe von " + body["cmd"]})
                return self._json({"detail": "unbekannt"}, 404)

            def _json(self, data, status=200):
                b = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            do_GET = do_POST = _handle

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
