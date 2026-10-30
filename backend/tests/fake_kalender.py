"""Ein kleiner Ersatz für den Kalender-Dienst: zeichnet alle Anfragen auf und antwortet mit festen Antworten (wie fake_github.py)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class FakeKalender:
    def __init__(self):
        self.requests: list[dict] = []
        self.mode = "ok"            # ok | drop (Verbindung ohne Antwort schließen = Dienst nicht erreichbar) | error (500)
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
                outer.requests.append({"method": self.command, "path": u.path, "query": q, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if outer.mode == "drop":
                    self.close_connection = True
                    return
                if outer.mode == "error":
                    return self._json({"detail": "kaputt"}, 500)
                if u.path.endswith("/stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("cache-control", "no-cache")
                    self.end_headers()
                    for i in range(3):
                        self.wfile.write(f'data: {json.dumps({"type": "changed", "n": i})}\n\n'.encode())
                        self.wfile.flush()
                        time.sleep(0.15)
                    self.close_connection = True
                    return
                p = u.path.replace("/api/v1", "", 1)
                if p == "/calendars":
                    return self._json({"calendars": [{"id": "c1", "name": "Privat", "color": "blau", "kind": "normal", "is_default": True, "agent_access": "write", "events": 2}]})
                if p == "/meta":
                    return self._json({"name": "IDA Kalender", "version": "fake1", "api": 2})
                if p == "/events" and self.command == "GET":
                    d = q.get("from", "2026-10-30")
                    return self._json({"events": [{"id": "ev1", "event_id": "ev1", "calendar_id": "c1", "calendar": "Privat", "title": "Fake-Termin", "start": d, "end": d, "all_day": True,
                                                   "location": "", "color": "blau", "created_by": "user"}]})
                if p == "/events" and self.command == "POST":
                    b = body or {}
                    ev = {"id": "ev9", "event_id": "ev9", "calendar_id": "c1", "calendar": "Privat", "title": b.get("title", ""), "start": b.get("start", ""), "end": b.get("end") or b.get("start", ""),
                          "all_day": False, "location": b.get("location", ""), "color": "blau", "recurring": False, "recurrence_text": "", "created_by": self.headers.get("x-kalender-by", "user")}
                    return self._json({"event": ev, "change_id": 7, "conflicts": []})
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
        self.mode = "ok"
