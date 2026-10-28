"""Stand-in for github.com + api.github.com in tests (Device Flow, /user, repos, pull requests).

Repos are bare git repos in a temp dir; their clone_url is a file:// URL, so cloning and pushing work offline.
"""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

GOOD = {"gho_device", "ghp_good"}  # tokens /user accepts


class FakeGitHub:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.polls = 0
        self.pulls: list[dict] = []
        self.repos: dict[str, dict] = {}
        self.add_repo("octo/hello", files={"README.md": "# Hallo\n"})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def add_repo(self, full: str, files: dict | None = None, private: bool = True) -> dict:
        bare = self.bare(full)
        bare.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        if files:
            work = self.root / "seed" / full.replace("/", "__")
            subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
            for name, text in files.items():
                (work / name).write_text(text, encoding="utf8")
            env = ["-c", "user.name=Seed", "-c", "user.email=seed@example.com"]
            subprocess.run(["git", *env, "add", "-A"], cwd=work, check=True)
            subprocess.run(["git", *env, "commit", "-q", "-m", "init"], cwd=work, check=True)
            subprocess.run(["git", "push", "-q", str(bare), "main"], cwd=work, check=True)
        r = {"full_name": full, "name": full.split("/")[1], "private": private, "description": "Testrepo", "default_branch": "main",
             "clone_url": bare.resolve().as_uri(), "html_url": f"https://github.com/{full}", "updated_at": "2026-10-01T10:00:00Z"}
        self.repos[full] = r
        return r

    def bare(self, full: str) -> Path:
        return self.root / (full + ".git")

    def _handler(self):
        gh = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body, headers: dict | None = None):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self) -> dict:
                raw = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
                if "json" in (self.headers.get("content-type") or ""):
                    return json.loads(raw or "{}")
                return {k: v[0] for k, v in parse_qs(raw).items()}

            def _token(self) -> str | None:
                h = self.headers.get("authorization") or ""
                return h.split(" ", 1)[1] if " " in h else None

            def do_POST(self):
                path, body = urlparse(self.path).path, self._body()
                if path == "/login/device/code":
                    if body.get("client_id") != "Iv1.testclient":
                        return self._send(404, {"error": "Not Found"})
                    gh.polls = 0
                    return self._send(200, {"device_code": "dc-1", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device",
                                            "expires_in": 900, "interval": 1})
                if path == "/login/oauth/access_token":
                    gh.polls += 1
                    if gh.polls == 1:
                        return self._send(200, {"error": "authorization_pending"})
                    if gh.polls == 2:
                        return self._send(200, {"error": "slow_down", "interval": 1})
                    return self._send(200, {"access_token": "gho_device", "token_type": "bearer", "scope": "repo,workflow"})
                if self._token() not in GOOD:
                    return self._send(401, {"message": "Bad credentials"})
                if path == "/user/repos":
                    full = f"octo/{body['name']}"
                    if full in gh.repos:
                        return self._send(422, {"message": "Repository creation failed.", "errors": [{"message": "name already exists on this account"}]})
                    return self._send(201, gh.add_repo(full, files={"README.md": f"# {body['name']}\n"} if body.get("auto_init") else None,
                                                       private=body.get("private", True)))
                if path.startswith("/repos/") and path.endswith("/pulls"):
                    pr = {"number": len(gh.pulls) + 1, "html_url": f"https://github.com{path[6:-6]}/pull/{len(gh.pulls) + 1}", "title": body["title"], **body}
                    gh.pulls.append(pr)
                    return self._send(201, pr)
                return self._send(404, {"message": "Not Found"})

            def do_GET(self):
                path = urlparse(self.path).path
                if self._token() not in GOOD:
                    return self._send(401, {"message": "Bad credentials"})
                if path == "/user":
                    return self._send(200, {"id": 42, "login": "octo", "name": "Octo Cat", "avatar_url": "https://example.com/a.png"},
                                      {"x-oauth-scopes": "repo, workflow, read:user"})
                if path == "/user/repos":
                    return self._send(200, list(gh.repos.values()))
                return self._send(404, {"message": "Not Found"})

        return H
