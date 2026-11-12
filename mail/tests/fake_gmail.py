"""Ersatz für Gmail (REST) und den Token-Endpunkt des Dienstes "google": zeichnet Anfragen auf, antwortet mit festen Daten."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def b64(s) -> str:
    return base64.urlsafe_b64encode(s if isinstance(s, bytes) else s.encode()).decode().rstrip("=")


def hdrs(**kw):
    return [{"name": k.replace("_", "-"), "value": v} for k, v in kw.items()]


ME = "simon@example.com"
BIG_TEXT = "Großer Textteil " * 50

THREAD = {"id": "t1", "historyId": "100", "messages": [
    {"id": "m1", "threadId": "t1", "labelIds": ["INBOX"], "internalDate": "1700000000000", "snippet": "Hallo Simon",
     "payload": {"mimeType": "multipart/mixed", "headers": hdrs(From="Lea Lehrer <lea@schule.de>", To=f"Simon <{ME}>", Subject="Projektarbeit",
                                                                  Date="Mon, 13 Nov 2023 10:00:00 +0100", Message_ID="<m1@schule.de>"),
                 "parts": [
                     {"mimeType": "multipart/related", "parts": [
                         {"mimeType": "text/html", "headers": hdrs(Content_Type='text/html; charset="iso-8859-1"'),
                          "body": {"data": b64("<p>Grüße <img src=\"cid:logo1\"> https://schule.de</p>".encode("latin-1"))}},
                         {"mimeType": "image/png", "filename": "logo.png", "headers": hdrs(Content_ID="<logo1>", Content_Disposition="inline"),
                          "body": {"attachmentId": "alogo", "size": 10}},
                     ]},
                     {"mimeType": "application/pdf", "filename": "Themenblatt.pdf", "headers": [], "body": {"attachmentId": "apdf", "size": 2048}},
                 ]}},
    {"id": "m2", "threadId": "t1", "labelIds": ["INBOX", "UNREAD", "STARRED"], "internalDate": "1700000600000", "snippet": "Danke",
     "payload": {"mimeType": "text/plain", "headers": hdrs(From=f"Simon <{ME}>", To="Lea Lehrer <lea@schule.de>", Cc="Max <max@x.de>, Mia <mia@x.de>",
                                                             Subject="Re: Projektarbeit", Message_ID="<m2@gmail.com>", References="<m1@schule.de>"),
                 "body": {"attachmentId": "abig", "size": 900}}},
]}


class FakeGmail:
    def __init__(self):
        self.requests: list[dict] = []
        self.connected = True
        self.expire_once = False
        self.history: list[dict] = []
        self.tokens = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _handle(self):
                u = urlparse(self.path)
                q = {k: v if len(v) > 1 else v[0] for k, v in parse_qs(u.query).items()}
                n = int(self.headers.get("content-length", 0) or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                p = u.path
                outer.requests.append({"method": self.command, "path": p, "query": q, "body": body, "auth": self.headers.get("authorization", "")})
                if p == "/api/v1/token/gmail":
                    if not outer.connected:
                        return self._json({"detail": "Noch nicht mit Google verbunden"}, 409)
                    outer.tokens += 1
                    return self._json({"access_token": f"tok{outer.tokens}", "expires_in": 3000})
                p = p.removeprefix("/gmail")
                if outer.expire_once and self.headers.get("authorization") == "Bearer tok1":
                    outer.expire_once = False
                    return self._json({"error": {"message": "expired"}}, 401)
                if p == "/profile":
                    return self._json({"emailAddress": ME, "historyId": "100"})
                if p == "/settings/sendAs":
                    return self._json({"sendAs": [{"sendAsEmail": ME, "displayName": "Simon", "isDefault": True, "isPrimary": True},
                                                  {"sendAsEmail": "info@kulbarts.com", "displayName": "", "verificationStatus": "accepted"}]})
                if p == "/labels":
                    return self._json({"labels": [{"id": "INBOX", "type": "system"}, {"id": "SENT", "type": "system"}, {"id": "TRASH", "type": "system"},
                                                  {"id": "Label_1", "name": "Schule", "type": "user", "color": {"backgroundColor": "#16a765"}}]})
                if p.startswith("/labels/"):
                    i = p.split("/")[2]
                    return self._json({"id": i, "threadsUnread": 3 if i == "INBOX" else 1, "threadsTotal": 10})
                if p == "/threads" and self.command == "GET":
                    return self._json({"threads": [{"id": "t1"}], "nextPageToken": "p2", "resultSizeEstimate": 1})
                if p == "/threads/t1":
                    return self._json(THREAD)
                if p.endswith("/modify") or p.endswith("/trash") or p.endswith("/untrash"):
                    return self._json({"id": p.split("/")[2]})
                if p == "/messages/m2/attachments/abig":
                    return self._json({"data": b64(BIG_TEXT)})
                if p == "/messages/m1/attachments/apdf":
                    return self._json({"data": b64(b"%PDF-1.4 fake")})
                if p == "/messages/m1":
                    return self._json(THREAD["messages"][0])
                if p.startswith("/messages/") and q.get("format") == "metadata":
                    return self._json({"id": p.split("/")[2], "payload": {"headers": hdrs(From="Paul <p@x.de>", Subject="Neu!")}})
                if p == "/messages/send":
                    return self._json({"id": "sent1", "threadId": body.get("threadId") or "tnew"})
                if p == "/drafts" and self.command == "POST":
                    return self._json({"id": "d1", "message": {"id": "md1", "threadId": "td1"}})
                if p.startswith("/drafts/") and self.command == "PUT":
                    return self._json({"id": p.split("/")[2], "message": {"id": "md1", "threadId": "td1"}})
                if p.startswith("/drafts/") and self.command == "DELETE":
                    self.send_response(204)
                    self.end_headers()
                    return
                if p == "/drafts":
                    return self._json({"drafts": []})
                if p == "/history":
                    if q.get("startHistoryId") == "1":
                        return self._json({"error": {"message": "too old"}}, 404)
                    return self._json({"history": outer.history, "historyId": "101" if outer.history else q.get("startHistoryId")})
                return self._json({"error": {"message": f"unbekannt {p}"}}, 404)

            do_GET = do_POST = do_PUT = do_DELETE = _handle

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def last(self, path: str, method: str | None = None) -> dict:
        return next(r for r in reversed(self.requests) if r["path"].endswith(path) and (method is None or r["method"] == method))
