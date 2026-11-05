"""Code area: switch, GitHub sign-in, projects (clone/new/publish), threads with real tools, approvals, git, Ida's access."""
import json
import subprocess
import threading
import time

import pytest

from .conftest import chat, wait

API = "/api/v1"


def text(ev) -> str:
    return "".join(e.get("text", "") for e in ev)


def done(ev) -> dict:
    return next(e["done"] for e in ev if "done" in e)


def ready(client, pid):
    return wait(lambda: (p := client.get(f"{API}/code/projects/{pid}").json())["status"] != "cloning" and p)


@pytest.fixture(scope="module")
def code_on(client):
    client.put(f"{API}/code/settings", json={"enabled": True})
    yield
    client.put(f"{API}/code/settings", json={"enabled": False})


def test_code_area_is_off_by_default(client):
    """Off until the user switches it on – code threads are real shell access and IDA has no login."""
    assert client.get(f"{API}/bootstrap").json()["code_enabled"] is False
    assert client.get(f"{API}/code/settings").json()["enabled"] is False
    r = client.get(f"{API}/code/projects")
    assert r.status_code == 400 and "ausgeschaltet" in r.json()["detail"]
    assert client.post(f"{API}/github/login").status_code == 400
    assert client.post(f"{API}/code/projects", json={"name": "x"}).status_code == 400


def test_github_sign_in_device_flow_and_token(client, code_on, github):
    """Device Flow: user code -> confirmed on github.com -> connected; the token never reaches the browser."""
    st = client.get(f"{API}/github/status").json()
    assert not st["connected"] and not st["client_id"]
    assert "Client ID" in client.post(f"{API}/github/login").json()["detail"]
    client.put(f"{API}/github/client-id", json={"client_id": "Iv1.testclient"})
    r = client.post(f"{API}/github/login").json()
    assert r["user_code"] == "ABCD-1234" and r["verification_uri"].endswith("/login/device")
    assert client.get(f"{API}/github/status").json()["pending"]["user_code"] == "ABCD-1234"
    # the server polls: authorization_pending, slow_down, then the token
    st = wait(lambda: (s := client.get(f"{API}/github/status").json())["connected"] and s, timeout=30)
    assert st["login"] == "octo" and st["name"] == "Octo Cat" and "repo" in st["scopes"] and st["via"] == "device" and not st["pending"]
    for url in ("/github/status", "/code/settings", "/bootstrap"):
        assert "gho_device" not in client.get(f"{API}{url}").text
    repos = client.get(f"{API}/github/repos?q=hello").json()
    assert [x["full_name"] for x in repos] == ["octo/hello"]
    # sign out, then the token way
    assert not client.delete(f"{API}/github").json()["connected"]
    assert client.get(f"{API}/github/repos").status_code == 400
    r = client.post(f"{API}/github/token", json={"token": "ghp_wrong"})
    assert r.status_code == 400 and "neu anmelden" in r.json()["detail"]
    assert client.post(f"{API}/github/token", json={"token": "ghp_good"}).json()["login"] == "octo"


def test_projects_clone_new_and_empty(client, code_on, github):
    """Clone one of your repos, create a new GitHub repo, or start empty – each is a git repo in data/projects."""
    hello = github.repos["octo/hello"]
    p = client.post(f"{API}/code/projects", json={"repo_url": hello["clone_url"]}).json()
    assert p["name"] == "hello" and p["status"] in ("cloning", "ready")
    p = ready(client, p["id"])
    assert p["status"] == "ready" and p["default_branch"] == "main" and p["git"]["clean"] and p["git"]["branch"] == "main"
    # new repo on GitHub (with README), cloned right away
    n = client.post(f"{API}/code/projects", json={"new_repo": {"name": "neu", "private": True}}).json()
    assert ready(client, n["id"])["status"] == "ready" and "octo/neu" in github.repos
    r = client.post(f"{API}/code/projects", json={"new_repo": {"name": "neu"}})
    assert r.status_code == 400 and "already exists" in r.json()["detail"]
    # empty local project
    e = client.post(f"{API}/code/projects", json={"name": "Leer"}).json()
    assert e["status"] == "ready" and client.get(f"{API}/code/projects/{e['id']}").json()["git"]["has_commits"] is False
    # bad addresses
    assert client.post(f"{API}/code/projects", json={"repo_url": "ssh://x/y"}).status_code == 400
    assert "Zugangsdaten" in client.post(f"{API}/code/projects", json={"repo_url": "https://user:pw@github.com/a/b"}).json()["detail"]
    broken = client.post(f"{API}/code/projects", json={"repo_url": "file:///gibt/es/nicht.git"}).json()
    assert ready(client, broken["id"])["status"] == "error"
    names = [x["name"] for x in client.get(f"{API}/code/projects").json()]
    assert {"hello", "neu", "Leer"} <= set(names)
    for x in (n, e, broken):
        assert client.delete(f"{API}/code/projects/{x['id']}").json()["ok"]


def test_project_path_never_leaves_projects_dir(server):
    from app.models import CodeProject
    from app.services.code import projects
    from app.services.errors import Invalid
    with pytest.raises(Invalid):
        projects.path_of(CodeProject(id="../../etc", name="x"))
    assert projects.path_of(CodeProject(id="abc", name="x")).parent == projects.root()


def _project(client, github, name="hello"):
    p = next((x for x in client.get(f"{API}/code/projects").json() if x["name"] == name), None)
    if not p:
        p = client.post(f"{API}/code/projects", json={"repo_url": github.repos[f"octo/{name}"]["clone_url"]}).json()
    return ready(client, p["id"])


def test_code_thread_runs_in_the_project_with_real_tools(client, code_on, github):
    """A thread is a chat of the main agent with kind=code: cwd = project folder, Claude's tools, git identity."""
    p = _project(client, github)
    ev = chat(client, project_id=p["id"], text="PWD wo bin ich", permission_mode="full")
    out = text(ev)
    c = ev[0]["chat"]
    assert c["kind"] == "code" and c["project_id"] == p["id"] and c["permission_mode"] == "full" and c["agent_id"] == "ida"
    from app.services.code import projects
    assert f"cwd={projects.root() / p['id']} " in out and "mode=bypassPermissions" in out and "tools=False" in out and "prompt_tool=None" in out
    assert "git_name=Octo Cat" in out and "gh=True" in out and "timeout=" in out
    # "ask" (default): Claude asks the permission tool of the internal MCP
    ev = chat(client, chat_id=c["id"], text="PWD", permission_mode="ask")
    assert "mode=default" in text(ev) and "prompt_tool=mcp__ida__code_approve" in text(ev)
    ev = chat(client, chat_id=c["id"], text="PWD", permission_mode="edits")
    assert "mode=acceptEdits" in text(ev)
    # threads are listed in the project, not among the normal chats
    assert c["id"] in [t["id"] for t in client.get(f"{API}/code/projects/{p['id']}/threads").json()]
    assert c["id"] not in [x["id"] for x in client.get(f"{API}/chats").json()]
    assert client.patch(f"{API}/code/threads/{c['id']}", json={"title": "Erster Thread", "permission_mode": "ask"}).json()["permission_mode"] == "ask"
    # code area off: the thread cannot run
    client.put(f"{API}/code/settings", json={"enabled": False})
    assert client.post(f"{API}/chat", json={"chat_id": c["id"], "text": "x"}).status_code == 400
    client.put(f"{API}/code/settings", json={"enabled": True})


def test_code_thread_takes_any_file(client, code_on, github):
    """Code app (any=true): every format goes; Claude finds it in the project under .ida/anhaenge/, kept out of git."""
    p = _project(client, github)
    up = lambda name, data, q="?any=true": client.post(f"{API}/uploads{q}", files={"file": (name, data, "application/octet-stream")})  # noqa: E731
    z = up("daten.zip", b"PK\x03\x04 irgendwas")
    assert z.status_code == 201 and z.json()["kind"] == "file"
    raw = up("bild.bin", bytes(range(256)) * 8).json()  # binary with a text ending is not refused either
    assert raw["kind"] == "file"
    assert up("doc.docx", b"PK\x03\x04 word", q="").status_code == 400  # without any=true as before
    ev = chat(client, project_id=p["id"], text="Pack das aus", files=[z.json()["id"], raw["id"]], permission_mode="full")
    out = text(ev)
    assert '<anhang name="daten.zip" pfad=".ida/anhaenge/daten.zip"' in out and 'pfad=".ida/anhaenge/bild.bin"' in out
    from app.services.code import projects
    folder = projects.root() / p["id"]
    assert (folder / ".ida/anhaenge/daten.zip").read_bytes() == b"PK\x03\x04 irgendwas"
    assert ".ida/" in (folder / ".git/info/exclude").read_text().splitlines()
    assert ".ida" not in subprocess.run(["git", "status", "--porcelain"], cwd=folder, capture_output=True, text=True).stdout
    # only as a download, never shown in the page
    r = client.get(f"{API}/files/{z.json()['id']}")
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["content-disposition"].startswith("attachment")
    # a normal chat cannot use such a file (the model there has no tools to open it)
    other = up("noch.zip", b"PK\x03\x04 x").json()
    assert client.post(f"{API}/chat", json={"text": "x", "files": [other["id"]]}).status_code == 400


def _start(client, body):
    """Starts an answer and leaves (like a browser that goes away); returns the chat id."""
    with client.stream("POST", f"{API}/chat", json=body) as r:
        assert r.status_code == 200, r.read()
        return json.loads(next(r.iter_lines()))["chat"]["id"]


def test_approvals_wait_for_the_user(client, code_on, github):
    """In "ask" mode an edit waits – with no time limit – until the user allows it; denying keeps the file untouched."""
    p = _project(client, github)
    cid = _start(client, {"project_id": p["id"], "text": "WRITE hallo.txt Hallo Welt"})
    a = wait(lambda: client.get(f"{API}/code/approvals?chat_id={cid}").json())[0]
    assert a["tool"] == "Write" and a["input"]["content"] == "Hallo Welt"
    time.sleep(1.5)  # it keeps waiting
    c = client.get(f"{API}/chats/{cid}").json()
    assert c["running"] and c["approvals"][0]["id"] == a["id"]
    th = next(t for t in client.get(f"{API}/code/projects/{p['id']}/threads").json() if t["id"] == cid)
    assert th["running"] and th["waiting"] == 1
    # a browser that comes back sees the open request in the replayed answer
    box = {}

    def follow():
        with client.stream("GET", f"{API}/chats/{cid}/live") as r:
            box["ev"] = [json.loads(x) for x in r.iter_lines() if x]
    t = threading.Thread(target=follow)
    t.start()
    time.sleep(0.5)
    assert client.post(f"{API}/code/approvals/{a['id']}", json={"decision": "allow"}).status_code == 200
    t.join(20)
    ev = box["ev"]
    assert any(e.get("approval", {}).get("id") == a["id"] for e in ev)
    assert any(e.get("approval_done", {}).get("decision") == "allow" for e in ev)
    assert "hallo.txt geschrieben" in text(ev) and done(ev)["ok"]
    assert client.post(f"{API}/code/approvals/{a['id']}", json={"decision": "allow"}).status_code == 404  # answered already
    d = client.get(f"{API}/code/projects/{p['id']}/diff").json()
    assert "+Hallo Welt" in d["diff"] and [f["path"] for f in d["files"]] == ["hallo.txt"]
    # deny
    cid2 = _start(client, {"chat_id": cid, "text": "WRITE boese.txt nein"})
    a = wait(lambda: client.get(f"{API}/code/approvals?chat_id={cid2}").json())[0]
    client.post(f"{API}/code/approvals/{a['id']}", json={"decision": "deny"})
    wait(lambda: not client.get(f"{API}/chats/{cid}").json()["running"])
    assert "abgelehnt" in client.get(f"{API}/chats/{cid}").json()["messages"][-1]["text"]
    assert "boese.txt" not in [f["path"] for f in client.get(f"{API}/code/projects/{p['id']}/git").json()["files"]]
    # always: the next Write in this thread goes through without asking
    _start(client, {"chat_id": cid, "text": "WRITE a.txt eins"})
    a = wait(lambda: client.get(f"{API}/code/approvals?chat_id={cid}").json())[0]
    client.post(f"{API}/code/approvals/{a['id']}", json={"decision": "always"})
    wait(lambda: not client.get(f"{API}/chats/{cid}").json()["running"])
    ev = chat(client, chat_id=cid, text="WRITE b.txt zwei")
    assert "b.txt geschrieben" in text(ev) and not any("approval" in e for e in ev)
    # stopping while a request waits ends the answer
    cid3 = _start(client, {"project_id": p["id"], "text": "WRITE c.txt drei"})
    wait(lambda: client.get(f"{API}/code/approvals?chat_id={cid3}").json())
    assert client.post(f"{API}/chats/{cid3}/stop").json()["ok"]
    wait(lambda: not client.get(f"{API}/chats/{cid3}").json()["running"])
    assert not client.get(f"{API}/code/approvals?chat_id={cid3}").json()
    assert "c.txt" not in [f["path"] for f in client.get(f"{API}/code/projects/{p['id']}/git").json()["files"]]


def _agy_login(client):
    """Create an Antigravity account and complete the fake Google sign-in."""
    from urllib.parse import parse_qs, urlparse
    a = client.post(f"{API}/accounts", json={"provider": "antigravity"}).json()
    st = client.post(f"{API}/accounts/{a['id']}/antigravity/login").json()
    q = parse_qs(urlparse(st["url"]).query)
    redirect, state = q["redirect_uri"][0], q["state"][0]
    client.post(f"{API}/accounts/{a['id']}/antigravity/callback", json={"url": f"{redirect}?code=abc&state={state}&scope=x"})
    return a


def test_antigravity_code_thread_uses_project_and_approvals(client, code_on, github):
    """Code threads accept agy: models: cwd = project, ask mode waits on the same approval UI as Claude."""
    from app.services.code import projects
    a = _agy_login(client)
    p = _project(client, github)
    path = projects.root() / p["id"]
    ev = chat(client, project_id=p["id"], text="PWD wo bin ich", model="agy:gemini-3-flash",
              account_id=a["id"], permission_mode="full")
    out = text(ev)
    assert done(ev)["ok"] and f"cwd={path}" in out and "mode=bypassPermissions" in out
    # ask mode: WRITE waits for the user like Claude's code_approve
    cid = _start(client, {"project_id": p["id"], "text": "WRITE agy.txt von Gemini", "model": "agy:gemini-3-flash",
                          "account_id": a["id"], "permission_mode": "ask"})
    appr = wait(lambda: client.get(f"{API}/code/approvals?chat_id={cid}").json())[0]
    assert appr["tool"] == "Write" and "von Gemini" in appr["input"]["content"]
    assert client.post(f"{API}/code/approvals/{appr['id']}", json={"decision": "allow"}).status_code == 200
    wait(lambda: not client.get(f"{API}/chats/{cid}").json()["running"])
    assert (path / "agy.txt").read_text(encoding="utf8") == "von Gemini"
    # full mode writes without asking
    ev = chat(client, chat_id=cid, text="WRITE free.txt ok", model="agy:gemini-3-flash",
              account_id=a["id"], permission_mode="full")
    assert "free.txt geschrieben" in text(ev) and not any("approval" in e for e in ev)
    assert (path / "free.txt").read_text(encoding="utf8") == "ok"
    client.delete(f"{API}/accounts/{a['id']}")


def test_git_commit_push_branch_and_pr(client, code_on, github):
    """Changes -> commit (as the GitHub account) -> push; branch; PR only with a GitHub remote."""
    p = _project(client, github)
    st = client.get(f"{API}/code/projects/{p['id']}/git").json()
    assert st["files"] and st["github"] == "octo" and "main" in st["branches"]
    # short commit message from the diff (fake Claude echoes the prompt – we only need a non-empty line)
    sug = client.post(f"{API}/code/projects/{p['id']}/commit-message", json={}).json()
    assert sug["message"] and sug["model"]
    assert client.post(f"{API}/code/projects/{p['id']}/commit", json={"message": " "}).status_code == 400
    r = client.post(f"{API}/code/projects/{p['id']}/commit", json={"message": "Dateien von IDA", "push": True}).json()
    assert r["sha"] and r["pushed"] == "main" and r["files"] >= 2
    assert client.get(f"{API}/code/projects/{p['id']}/git").json()["clean"]
    log = subprocess.run(["git", "log", "-1", "--format=%an <%ae>|%s", "main"], cwd=github.bare("octo/hello"), capture_output=True, text=True).stdout
    assert log.strip() == "Octo Cat <42+octo@users.noreply.github.com>|Dateien von IDA"
    assert client.post(f"{API}/code/projects/{p['id']}/commit", json={"message": "leer"}).json()["detail"] == "Nichts zu committen"
    assert client.post(f"{API}/code/projects/{p['id']}/commit-message", json={}).json()["detail"] == "Keine Änderungen"
    # branches
    assert client.post(f"{API}/code/projects/{p['id']}/branch", json={"name": "kein gültiger..name"}).status_code == 400
    assert client.post(f"{API}/code/projects/{p['id']}/branch", json={"name": "ida/feature"}).json()["branch"] == "ida/feature"
    # PR needs a GitHub remote (this test repo is a local file:// one)
    r = client.post(f"{API}/code/projects/{p['id']}/pr", json={"title": "Feature"})
    assert r.status_code == 400 and "GitHub-Repo" in r.json()["detail"]
    client.post(f"{API}/code/projects/{p['id']}/branch", json={"name": "main", "create": False})


def test_github_remote_names_and_auth_env():
    from app.services.code import git
    from app.config import get_settings
    host = get_settings().github_url
    assert git.full_name(f"{host}/octo/hello.git") == "octo/hello"
    assert git.full_name(f"{host}/octo/hello") == "octo/hello"
    assert git.full_name("file:///x/y.git") is None
    env = git.auth_env({"token": "tok", "login": "octo", "name": "Octo", "email": "e@x"})
    assert env["GIT_CONFIG_KEY_0"] == f"http.{host}/.extraheader" and "tok" not in env["GIT_CONFIG_VALUE_0"]
    assert env["GH_TOKEN"] == "tok" and env["GIT_AUTHOR_NAME"] == "Octo"
    assert git._clean("fatal: https://x-access-token:abc@github.com/a AUTHORIZATION: basic Zm9v") == "fatal: https://github.com/a AUTHORIZATION: basic ***"


def test_publish_local_project_to_github(client, code_on, github):
    p = client.post(f"{API}/code/projects", json={"name": "Lokal"}).json()
    ev = chat(client, project_id=p["id"], text="WRITE main.py print(1)", permission_mode="edits")
    assert "geschrieben" in text(ev)
    client.post(f"{API}/code/projects/{p['id']}/commit", json={"message": "Start"})
    r = client.post(f"{API}/code/projects/{p['id']}/publish", json={"name": "lokal", "private": True}).json()
    assert r["repo_url"] == github.repos["octo/lokal"]["clone_url"]
    log = subprocess.run(["git", "log", "-1", "--format=%s", "main"], cwd=github.bare("octo/lokal"), capture_output=True, text=True).stdout
    assert log.strip() == "Start"
    assert client.post(f"{API}/code/projects/{p['id']}/publish", json={"name": "lokal"}).status_code == 400
    client.delete(f"{API}/code/projects/{p['id']}")


def test_shared_memory_and_ida_finds_code_threads(client, code_on, github):
    """Code threads write into the same memory; in a normal chat Ida finds the projects and threads when she looks."""
    p = _project(client, github)
    ev = chat(client, project_id=p["id"], text='TOOL memory_save {"text": "Der Nutzer nimmt immer pnpm statt npm", "scope": "shared"}')
    assert '"ok": true' in text(ev)
    ev = chat(client, text="SHOWSYS weisst du was ueber pnpm?")
    assert "immer pnpm statt npm" in text(ev)  # normal chat: same (shared) memory
    ev = chat(client, text='TOOL code_projects {}')
    assert '"hello"' in text(ev) and '"threads"' in text(ev)
    ev = chat(client, text='TOOL chat_search {"query": "pnpm", "bereich": "code"}')
    out = text(ev)
    assert '"bereich": "code"' in out and '"projekt": "hello"' in out
    ev = chat(client, text='TOOL chat_search {"query": "pnpm", "bereich": "chat"}')
    assert '"bereich": "code"' not in text(ev)
    # sub-agents do not get code_projects, and code_approve only exists inside a code thread
    assert "Keine Berechtigung" in text(chat(client, agent_id="lea", text='TOOL code_projects {}'))
    ev = chat(client, text='TOOL code_approve {"tool_name": "Bash", "input": {}}')
    assert "Keine Berechtigung" in text(ev) or "deny" in text(ev)
    for c in client.get(f"{API}/chats").json():
        client.delete(f"{API}/chats/{c['id']}")


def test_code_commit_tool_writes_message_from_diff(client, code_on, github):
    """Agent commits via code_commit; without message a cheap model call uses only the diff."""
    from app.services.code import projects
    p = client.post(f"{API}/code/projects", json={"name": "CommitTool"}).json()
    folder = projects.root() / p["id"]
    (folder / "note.txt").write_text("hallo commit", encoding="utf-8")
    ev = chat(client, project_id=p["id"], text='TOOL code_commit {}', permission_mode="full")
    out = text(ev)
    assert '"ok": true' in out and '"sha"' in out and '"message"' in out and '"model"' in out
    assert client.get(f"{API}/code/projects/{p['id']}/git").json()["clean"]
    # no changes left → tool reports that
    ev2 = chat(client, project_id=p["id"], text='TOOL code_commit {"message": "nochmal"}', permission_mode="full")
    assert "Nichts zu committen" in text(ev2) or "Keine Änderungen" in text(ev2)


def test_delete_project_removes_threads_and_folder(client, code_on, github):
    from app.services.code import projects
    p = client.post(f"{API}/code/projects", json={"name": "Weg"}).json()
    ev = chat(client, project_id=p["id"], text="PWD")
    cid = ev[0]["chat"]["id"]
    folder = projects.root() / p["id"]
    assert folder.is_dir()
    assert client.delete(f"{API}/code/projects/{p['id']}").json()["ok"]
    assert not folder.exists()
    assert client.get(f"{API}/chats/{cid}").status_code == 404
    assert client.get(f"{API}/code/projects/{p['id']}").status_code == 404


def test_drafts_are_kept_on_the_server(client, code_on, github):
    """Unsent text in the input field: stored per thread / new thread, same on every device, gone when sent or deleted."""
    p = client.post(f"{API}/code/projects", json={"name": "Entwurf"}).json()
    new = f"{API}/code/drafts/p-{p['id']}"
    assert client.get(new).json() == {"text": "", "files": [], "at": None}
    r = client.put(new, json={"text": "Bau mir\neinen Test"}).json()
    assert r["text"] == "Bau mir\neinen Test" and r["at"]
    assert client.get(new).json()["text"] == "Bau mir\neinen Test"
    assert client.put(new, json={"text": "  "}).json()["text"] == ""
    assert client.get(new).json()["text"] == ""
    # a thread's draft goes away with the thread
    cid = chat(client, project_id=p["id"], text="PWD")[0]["chat"]["id"]
    client.put(f"{API}/code/drafts/t-{cid}", json={"text": "weiter"})
    assert client.get(f"{API}/code/drafts/t-{cid}").json()["text"] == "weiter"
    client.delete(f"{API}/chats/{cid}")
    assert client.get(f"{API}/code/drafts/t-{cid}").json()["text"] == ""
    for bad in ("x-1", "t-", "t-a.b", "t-" + "a" * 40):
        assert client.get(f"{API}/code/drafts/{bad}").status_code == 404
    client.delete(f"{API}/code/projects/{p['id']}")


def test_files_stay_in_drafts_and_queues(client, code_on, github):
    """Attached files wait with the draft or the queued message: shown with their name, not cleaned up after a day."""
    import asyncio
    from datetime import timedelta
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.config import get_settings
    from app.db import utcnow
    from app.models import ChatFile
    from app.services import files as file_svc

    up = lambda name: client.post(f"{API}/uploads", files={"file": (name, b"hallo", "text/plain")}).json()
    a, b, c, gone = up("a.txt"), up("b.txt"), up("c.txt"), up("weg.txt")
    p = _project(client, github)
    new = f"{API}/code/drafts/p-{p['id']}"
    # only files, no text: still a draft
    assert [f["name"] for f in client.put(new, json={"text": "", "files": [a["id"]]}).json()["files"]] == ["a.txt"]
    assert client.get(new).json()["files"][0]["id"] == a["id"]
    # a normal chat keeps the field, its files and the waiting messages
    d = f"{API}/chat-drafts/n-ida"
    client.put(d, json={"text": "hi", "files": [b["id"]], "queue": [{"text": "später", "skill": None, "files": [c["id"]]}]})
    got = client.get(d).json()
    assert got["text"] == "hi" and got["files"][0]["name"] == "b.txt" and got["queue"][0]["files"][0]["name"] == "c.txt"
    assert client.get(f"{API}/chat-drafts/x-1").status_code == 404

    cid = _start(client, {"project_id": p["id"], "text": "SLOW eins", "permission_mode": "full"})
    e = up("e.txt")
    item = client.post(f"{API}/code/queue/{cid}", json={"text": "mit Datei", "files": [e["id"]]}).json()["items"][0]
    assert item["attachments"][0]["name"] == "e.txt"

    async def age_and_purge():
        # eigene Verbindung ohne Pool: die der App gehören zu ihrer Event-Loop (asyncpg stürzt sonst ab)
        eng = create_async_engine(get_settings().db_url, poolclass=NullPool)
        try:
            async with AsyncSession(eng, expire_on_commit=False) as db:
                for i in (a, b, c, e, gone):
                    (await db.get(ChatFile, i["id"])).created_at = utcnow() - timedelta(days=3)
                await db.commit()
                await file_svc.purge(db)
                await db.commit()
                return {i["name"]: (await db.get(ChatFile, i["id"])) is not None for i in (a, b, c, e, gone)}
        finally:
            await eng.dispose()

    assert asyncio.run(age_and_purge()) == {"a.txt": True, "b.txt": True, "c.txt": True, "e.txt": True, "weg.txt": False}
    # queued files go out with their message
    wait(lambda: (x := client.get(f"{API}/chats/{cid}").json()) and not x["running"] and not x["queue"]["items"], timeout=40)
    last = [m for m in client.get(f"{API}/chats/{cid}").json()["messages"] if m["role"] == "user"][-1]
    assert last["text"] == "mit Datei" and last["meta"]["files"][0]["name"] == "e.txt"
    assert client.put(d, json={}).json() == {"text": "", "files": [], "queue": []}


def test_project_files_listing_and_preview(client, code_on, github):
    from app.services.code import projects
    p = client.post(f"{API}/code/projects", json={"name": "FilesTest"}).json()
    pid = p["id"]
    folder = projects.root() / pid

    # Create subfolder and test files
    (folder / "index.html").write_text("<!DOCTYPE html><html><body><h1>Hello World</h1></body></html>", encoding="utf-8")
    (folder / "style.css").write_text("body { color: red; }", encoding="utf-8")
    sub = folder / "src"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "app.js").write_text("console.log('test');", encoding="utf-8")

    # 1. Root files listing
    res = client.get(f"{API}/code/projects/{pid}/files")
    assert res.status_code == 200
    data = res.json()
    assert data["path"] == ""
    assert data["parent"] is None
    names = [e["name"] for e in data["entries"]]
    assert "src" in names and "index.html" in names and "style.css" in names
    # Folders first
    assert data["entries"][0]["name"] == "src" and data["entries"][0]["is_dir"] is True
    # .git should be filtered out
    assert ".git" not in names

    # 2. Subdirectory listing
    res_sub = client.get(f"{API}/code/projects/{pid}/files?path=src")
    assert res_sub.status_code == 200
    sub_data = res_sub.json()
    assert sub_data["path"] == "src"
    assert sub_data["parent"] == ""
    assert [e["name"] for e in sub_data["entries"]] == ["app.js"]

    # 3. Path traversal protection
    assert client.get(f"{API}/code/projects/{pid}/files?path=../").status_code == 400
    assert client.get(f"{API}/code/projects/{pid}/files?path=.git").status_code == 400
    assert client.get(f"{API}/code/projects/{pid}/file?path=../main.py").status_code == 400
    assert client.get(f"{API}/code/projects/{pid}/file?path=.git/config").status_code == 400

    # 4. File info & text preview
    f_res = client.get(f"{API}/code/projects/{pid}/file?path=index.html")
    assert f_res.status_code == 200
    f_data = f_res.json()
    assert f_data["name"] == "index.html"
    assert f_data["is_text"] is True
    assert "<h1>Hello World</h1>" in f_data["content"]
    assert f_data["mime"] == "text/html"

    # 5. Raw file endpoint
    raw_res = client.get(f"{API}/code/projects/{pid}/raw/index.html")
    assert raw_res.status_code == 200
    assert "text/html" in raw_res.headers.get("content-type", "")
    assert "<h1>Hello World</h1>" in raw_res.text

    raw_css = client.get(f"{API}/code/projects/{pid}/raw/style.css")
    assert raw_css.status_code == 200
    assert "text/css" in raw_css.headers.get("content-type", "")
    assert "color: red" in raw_css.text

    # Cleanup
    client.delete(f"{API}/code/projects/{pid}")


def test_github_actions_runs_jobs_and_log(client, code_on, github, monkeypatch):
    """Workflow runs of the repo, their jobs with steps and the plain-text log; local projects have no Actions."""
    from app.services.code import git
    p = _project(client, github)
    monkeypatch.setattr(git, "full_name", lambda url: "octo/hello")
    base = f"{API}/code/projects/{p['id']}/actions"
    runs = client.get(f"{base}/runs").json()
    assert runs["total"] == 1 and runs["runs"][0]["conclusion"] == "failure" and runs["runs"][0]["sha"] == "abcdef1"
    detail = client.get(f"{base}/runs/7").json()
    assert detail["jobs"][0]["steps"][0]["name"] == "pytest"
    log = client.get(f"{base}/jobs/70/log").json()
    assert "FAILED test_x" in log["log"] and not log["truncated"]
    monkeypatch.setattr(git, "full_name", lambda url: None)
    r = client.get(f"{base}/runs")
    assert r.status_code == 400 and "GitHub-Repo" in r.json()["detail"]


def test_queue_is_kept_on_the_server_and_sent_by_it(client, code_on, github):
    """Messages written while the AI works wait on the server; it sends them itself – also with no browser open."""
    p = _project(client, github)
    cid = _start(client, {"project_id": p["id"], "text": "SLOW eins", "permission_mode": "full"})
    q = f"{API}/code/queue/{cid}"
    assert client.post(q, json={"text": "zwei"}).json()["items"][0]["text"] == "zwei"
    client.post(q, json={"text": "drei", "model": "claude-haiku-5-5"})
    assert [x["text"] for x in client.get(f"{API}/chats/{cid}").json()["queue"]["items"]] == ["zwei", "drei"]
    # nobody follows the thread: the server works through the queue by itself
    wait(lambda: (c := client.get(f"{API}/chats/{cid}").json()) and not c["running"] and not c["queue"]["items"], timeout=40)
    msgs = client.get(f"{API}/chats/{cid}").json()["messages"]
    assert [m["text"] for m in msgs if m["role"] == "user"] == ["SLOW eins", "zwei", "drei"]
    assert "haiku" in msgs[-1]["text"] and msgs[-1]["text"].endswith("drei")
    # nothing runs: a queued message goes out right away
    client.post(q, json={"text": "vier"})
    wait(lambda: not client.get(f"{API}/chats/{cid}").json()["running"])
    assert client.get(f"{API}/chats/{cid}").json()["messages"][-2]["text"] == "vier"
    # a deliberate stop pauses the queue; "Jetzt senden" stops and sends that one next
    _start(client, {"chat_id": cid, "text": "SLOW fünf"})
    a = client.post(q, json={"text": "sechs"}).json()["items"][0]
    b = client.post(q, json={"text": "sieben"}).json()["items"][1]
    client.post(f"{API}/chats/{cid}/stop")
    wait(lambda: not client.get(f"{API}/chats/{cid}").json()["running"])
    time.sleep(0.5)
    assert len(client.get(q).json()["items"]) == 2  # paused
    assert client.delete(f"{q}/{a['id']}").json()["items"][0]["text"] == "sieben"
    r = client.post(q, json={"text": "SLOW acht"}).json()  # nothing runs: "sieben" goes out first, "SLOW acht" waits
    assert r["running"] and [x["text"] for x in r["items"]] == ["SLOW acht"]
    wait(lambda: (c := client.get(f"{API}/chats/{cid}").json()) and not c["running"] and not c["queue"]["items"], timeout=40)
    users = [m["text"] for m in client.get(f"{API}/chats/{cid}").json()["messages"] if m["role"] == "user"]
    assert users[-2:] == ["sieben", "SLOW acht"] and "sechs" not in users
    _start(client, {"chat_id": cid, "text": "SLOW neun"})
    n = client.post(q, json={"text": "zehn"}).json()["items"][0]
    client.post(f"{q}/{n['id']}/now")
    wait(lambda: (c := client.get(f"{API}/chats/{cid}").json()) and not c["running"] and not c["queue"]["items"], timeout=40)
    msgs = client.get(f"{API}/chats/{cid}").json()["messages"]
    assert msgs[-2]["text"] == "zehn" and msgs[-1]["text"].endswith("zehn")
    # a browser that comes back while the next one starts follows the new run
    _start(client, {"chat_id": cid, "text": "SLOW elf"})
    client.post(q, json={"text": "SLOW zwölf"})  # slow, so the test sees it running
    wait(lambda: client.get(f"{API}/chats/{cid}").json()["messages"][-1]["text"] == "SLOW zwölf", timeout=30)
    with client.stream("GET", f"{API}/chats/{cid}/live") as r:
        ev = [json.loads(x) for x in r.iter_lines() if x]
    assert text(ev).endswith("SLOW zwölf") and done(ev)["ok"]
    # the thread goes, its queue too
    client.delete(f"{API}/chats/{cid}")
    assert client.get(q).status_code == 404


def test_branch_off_a_thread_from_any_answer(client, code_on, github):
    """Abzweigen: a new thread with everything up to that answer; from the latest one it forks Claude's session."""
    p = _project(client, github)
    ev = chat(client, project_id=p["id"], text="eins", permission_mode="full")
    cid = ev[0]["chat"]["id"]
    chat(client, chat_id=cid, text="zwei")
    msgs = client.get(f"{API}/chats/{cid}").json()["messages"]
    first, last = msgs[1], msgs[3]
    assert client.post(f"{API}/chats/{cid}/fork", json={"message_id": msgs[0]["id"]}).status_code == 400  # not from a user message
    # from the latest answer: same session, forked on the next message (the original keeps its own)
    f = client.post(f"{API}/chats/{cid}/fork", json={"message_id": last["id"]}).json()
    assert f["kind"] == "code" and f["project_id"] == p["id"] and f["title"].startswith("↳ ") and f["id"] != cid
    fm = client.get(f"{API}/chats/{f['id']}").json()["messages"]
    assert [m["text"] for m in fm[:4]] == [m["text"] for m in msgs] and fm[-1]["role"] == "system"
    out = text(chat(client, chat_id=f["id"], text="drei im Zweig"))
    assert "FORK(" in out and "drei im Zweig" in out and "Bisheriger" not in out
    out = text(chat(client, chat_id=f["id"], text="vier"))
    assert "FORK(" not in out  # from now on its own session
    assert "FORK(" not in text(chat(client, chat_id=cid, text="weiter im Original"))
    # from an earlier answer: the conversation up to there goes along as text
    g = client.post(f"{API}/chats/{cid}/fork", json={"message_id": first["id"]}).json()
    out = text(chat(client, chat_id=g["id"], text="anders weiter"))
    assert "Bisheriger Gesprächsverlauf" in out and "Nutzer: eins" in out and "zwei" not in out.split("Neue Nachricht")[0]
    assert g["id"] in [t["id"] for t in client.get(f"{API}/code/projects/{p['id']}/threads").json()]


def test_server_stack_per_project_tools_via_dashboard(client, code_on, dashboard):
    """Per project a Docker stack of the server: the thread sees status/logs, runs commands and updates via the Dashboard (no SSH)."""
    p = client.post(f"{API}/code/projects", json={"name": "ServerStack"}).json()
    d = client.get(f"{API}/code/server-stacks").json()
    assert d["enabled"] and [s["name"] for s in d["stacks"]] == ["shop", "ida"] and d["stacks"][0]["services"] == ["app", "db"]
    # without a stack the tools say so
    ev = chat(client, project_id=p["id"], text="TOOL server_status {}", permission_mode="full")
    assert "kein Server-Stack" in text(ev)
    assert client.patch(f"{API}/code/projects/{p['id']}", json={"server_stack": "gibtsnicht"}).status_code == 400
    r = client.patch(f"{API}/code/projects/{p['id']}", json={"server_stack": "shop"}).json()
    assert r["server_stack"] == "shop" and r["name"] == "ServerStack"
    # the prompt tells the thread about the stack
    ev = chat(client, project_id=p["id"], text="SHOWSYS hallo", permission_mode="full")
    assert 'Docker-Stack "shop"' in text(ev) and "server_logs" in text(ev)
    ev = chat(client, project_id=p["id"], text="TOOL server_status {}", permission_mode="full")
    assert '"zustand": "running"' in text(ev) and "shop.example.com" in text(ev) and '"schutz": "access"' in text(ev)
    ev = chat(client, project_id=p["id"], text='TOOL server_logs {"service": "app", "grep": "error"}', permission_mode="full")
    assert "ERROR kaputt" in text(ev) and "laeuft" not in text(ev)
    ev = chat(client, project_id=p["id"], text='TOOL server_logs {}', permission_mode="full")
    assert "mehrere Dienste" in text(ev)
    ev = chat(client, project_id=p["id"], text='TOOL server_befehl {"service": "app", "befehl": "ls /app"}', permission_mode="full")
    assert "ausgabe von ls /app" in text(ev)
    assert any(x["path"] == "/api/v1/containers/c-app/exec" and x["body"]["cmd"] == "ls /app" for x in dashboard.requests)
    ev = chat(client, project_id=p["id"], text='TOOL server_aktion {"aktion": "update"}', permission_mode="full")
    assert '"ok": true' in text(ev) and "docker compose pull" in text(ev)
    assert any(x["path"] == "/api/v1/stacks/shop/action" and x["body"] == {"action": "update"} for x in dashboard.requests)
    # in "ask" mode a command waits for the user; denying runs nothing
    n = len(dashboard.requests)
    cid = _start(client, {"project_id": p["id"], "text": 'TOOL server_befehl {"service": "db", "befehl": "rm -rf /"}'})
    a = wait(lambda: client.get(f"{API}/code/approvals?chat_id={cid}").json())[0]
    assert a["tool"] == "Server: Befehl in shop" and a["input"]["befehl"] == "rm -rf /"
    client.post(f"{API}/code/approvals/{a['id']}", json={"decision": "deny"})
    wait(lambda: not client.get(f"{API}/chats/{cid}").json()["running"])
    assert "abgelehnt" in client.get(f"{API}/chats/{cid}").json()["messages"][-1]["text"]
    assert not any(x["path"].endswith("/exec") for x in dashboard.requests[n:])
    # remove the stack again
    assert client.patch(f"{API}/code/projects/{p['id']}", json={"server_stack": ""}).json()["server_stack"] is None
