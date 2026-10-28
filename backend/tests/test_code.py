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
    # only Claude models in the code area; a normal chat cannot become a code thread by accident
    r = client.post(f"{API}/chat", json={"project_id": p["id"], "text": "x", "model": "agy:gemini-3-flash"})
    assert r.status_code == 400
    assert client.patch(f"{API}/code/threads/{c['id']}", json={"title": "Erster Thread", "permission_mode": "ask"}).json()["permission_mode"] == "ask"
    # code area off: the thread cannot run
    client.put(f"{API}/code/settings", json={"enabled": False})
    assert client.post(f"{API}/chat", json={"chat_id": c["id"], "text": "x"}).status_code == 400
    client.put(f"{API}/code/settings", json={"enabled": True})


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


def test_git_commit_push_branch_and_pr(client, code_on, github):
    """Changes -> commit (as the GitHub account) -> push; branch; PR only with a GitHub remote."""
    p = _project(client, github)
    st = client.get(f"{API}/code/projects/{p['id']}/git").json()
    assert st["files"] and st["github"] == "octo" and "main" in st["branches"]
    assert client.post(f"{API}/code/projects/{p['id']}/commit", json={"message": " "}).status_code == 400
    r = client.post(f"{API}/code/projects/{p['id']}/commit", json={"message": "Dateien von IDA", "push": True}).json()
    assert r["sha"] and r["pushed"] == "main" and r["files"] >= 2
    assert client.get(f"{API}/code/projects/{p['id']}/git").json()["clean"]
    log = subprocess.run(["git", "log", "-1", "--format=%an <%ae>|%s", "main"], cwd=github.bare("octo/hello"), capture_output=True, text=True).stdout
    assert log.strip() == "Octo Cat <42+octo@users.noreply.github.com>|Dateien von IDA"
    assert client.post(f"{API}/code/projects/{p['id']}/commit", json={"message": "leer"}).json()["detail"] == "Nichts zu committen"
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
