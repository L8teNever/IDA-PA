"""Updates: Registry, Einordnung der Container, Zeitplan und was beim Aktualisieren wirklich aufgerufen wird."""
import asyncio
import base64
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import registry
from app.tools import stacks as stacks_mod
from app.tools import updates as up

from .conftest import ctr
from .test_server import server  # noqa: F401

TZ = ZoneInfo("Europe/Berlin")
OLD, NEW = "sha256:" + "a" * 64, "sha256:" + "b" * 64
HOOK = {up.HOOK: "python -m app.voice.idle"}


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp()


def settings(**kw):
    return {**up.DEFAULTS, **kw}


@pytest.fixture
def stack(settings, fake_docker):  # noqa: F811
    """Stack „shop“ mit Compose-Datei: web und db aus der Registry, vpn zieht browser mit, app wird lokal gebaut."""
    d = settings.stack_dirs[0] / "shop"
    d.mkdir()
    (d / "compose.yaml").write_text("""services:
  web: {image: "acme/web:1"}
  db: {image: "postgres:16"}
  app: {image: "nora:local", build: .}
  vpn: {image: "acme/vpn:1"}
  browser: {image: "acme/browser:1", network_mode: "service:vpn"}
""")
    kw = {"project": "shop", "wd": str(d), "files": str(d / "compose.yaml")}
    fake_docker.containers = [ctr("1", "web", service="web", image="acme/web:1", **kw), ctr("2", "db", service="db", image="postgres:16", **kw),
                              ctr("3", "app", service="app", image="nora:local", **kw), ctr("4", "vpn", service="vpn", image="acme/vpn:1", **kw),
                              ctr("5", "browser", service="browser", image="acme/browser:1", **kw)]
    for c in fake_docker.containers:
        c["ImageID"] = f"sha256:{c['Names'][0][1:]}"
        fake_docker.images[c["ImageID"]] = {"Id": c["ImageID"], "RepoDigests": [f"x@{OLD}"]}
    return d


@pytest.fixture
def remote(monkeypatch):
    """Registry-Antworten je Image; was fehlt, gilt als unverändert."""
    answers = {}

    async def fake(_client, ref, _login=None):
        v = answers.get(f"{ref.repo}:{ref.tag}", OLD)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(registry, "digest", fake)
    return answers


@pytest.fixture
def cmds(monkeypatch):
    """Statt Compose: merkt sich die Befehle, `fail` lässt passende scheitern."""
    log = []
    log_fail = []

    async def fake_run(job, args, cwd=None, env=None):
        log.append(args)
        return not any(all(x in args for x in f) for f in log_fail)
    monkeypatch.setattr(up, "run_cmd", fake_run)
    monkeypatch.setattr(up, "HEALTH_STEP", 0)
    monkeypatch.setattr(up, "HEALTH_MIN", 0)
    monkeypatch.setattr(up, "HEALTH_MAX", 0.01)
    return Recorder(log, log_fail)


class Recorder:
    def __init__(self, log, fails):
        self.log, self.fails = log, fails

    def verbs(self, verb):
        return [a for a in self.log if verb in a]


async def wait(app):
    for t in list(app.state.jobs.tasks):
        await t


def row(rows, key):
    return next(r for r in rows if r["key"] == key)


# ---------- Registry ----------
@pytest.mark.parametrize("image,want", [
    ("nginx", (registry.HUB, "library/nginx", "latest")),
    ("nginx:alpine", (registry.HUB, "library/nginx", "alpine")),
    ("louislam/dockge:1", (registry.HUB, "louislam/dockge", "1")),
    ("docker.io/acme/web", (registry.HUB, "acme/web", "latest")),
    ("ghcr.io/l8tenever/ida-pa:latest", ("ghcr.io", "l8tenever/ida-pa", "latest")),
    ("registry.local:5000/a/b", ("registry.local:5000", "a/b", "latest")),
    ("localhost:5000/b:2", ("localhost:5000", "b", "2")),
])
def test_parse(image, want):
    r = registry.parse(image)
    assert (r.registry, r.repo, r.tag) == want and not r.pinned


def test_parse_pinned():
    assert registry.parse("nginx:1@sha256:abc").pinned


def test_logins_from_docker_config(tmp_path):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"auths": {"ghcr.io": {"auth": base64.b64encode(b"simon:geheim").decode()},
                                       "https://index.docker.io/v1/": {"auth": base64.b64encode(b"hub:pw").decode()}, "kaputt.io": {}}}))
    assert registry.load_auths(f) == {"ghcr.io": ("simon", "geheim"), registry.HUB: ("hub", "pw")}
    assert registry.load_auths(tmp_path / "fehlt.json") == {}


async def test_digest_with_token_login():
    seen = []

    def handler(req):
        seen.append(req.method)
        if req.url.host == "auth.example.com":
            assert req.url.params["scope"] == "repository:acme/web:pull" and req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"token": "t0k"})
        if req.headers.get("authorization") != "Bearer t0k":
            return httpx.Response(401, headers={"www-authenticate": 'Bearer realm="https://auth.example.com/token",service="reg",scope="repository:acme/web:pull"'})
        return httpx.Response(200, headers={"docker-content-digest": NEW})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await registry.digest(c, registry.parse("reg.example.com/acme/web:1"), ("u", "p")) == NEW
    assert "HEAD" in seen and seen.count("GET") == 1   # nur das Token wird geholt, das Manifest nicht


@pytest.mark.parametrize("status,kind", [(404, registry.NotThere), (403, registry.NotThere), (429, registry.RegistryError), (500, registry.RegistryError)])
async def test_digest_errors(status, kind):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(status))) as c:
        with pytest.raises(kind):
            await registry.digest(c, registry.parse("acme/web"))


# ---------- Zeitplan ----------
def test_daily_runs_once_at_the_time():
    s = settings(check="daily", check_at="03:00")
    assert not up.check_due(s, at(4, 3), at(5, 2, 59), TZ)
    assert up.check_due(s, at(4, 3), at(5, 3, 0), TZ)
    assert not up.check_due(s, at(5, 3), at(5, 3, 1), TZ)


def test_missed_time_waits_for_next_night():
    assert not up.check_due(settings(check="daily", check_at="03:00"), at(4, 3), at(5, 9), TZ)   # Server war um 3 Uhr aus: nicht tagsüber nachholen
    assert up.check_due(settings(check="daily", check_at="03:00"), at(4, 3), at(5, 3, 40), TZ)


def test_new_time_in_the_past_means_tomorrow():
    s = settings(check="daily", check_at="14:50", saved_at=at(5, 15))
    assert not up.check_due(s, 0, at(5, 15, 1), TZ)
    assert up.check_due(s, at(5, 15), at(6, 14, 50), TZ)


def test_interval_and_off():
    assert up.check_due(settings(every_hours=6), at(5, 3), at(5, 9), TZ)
    assert not up.check_due(settings(every_hours=6), at(5, 3), at(5, 8, 59), TZ)
    assert not up.check_due(settings(check="off"), 0, at(5, 9), TZ)
    assert not up.apply_due(settings(check="off", action="at"), 0, at(5, 3), TZ)


def test_next_times():
    n = up.next_times(settings(check="daily", check_at="03:00", action="at", apply_at="04:00"), {"last_check": 0}, at(5, 15), TZ)
    assert n == {"check": at(6, 3), "apply": at(6, 4)}


def test_scope():
    for scope, shop, web, other in (("all", True, True, True), ("except", False, False, True), ("only", True, True, False)):
        s = settings(scope=scope, picks=["shop"])
        assert (up.wanted(s, "shop", "db"), up.wanted(s, "shop", "web"), up.wanted(s, "x", "y")) == (shop, web, other)
    s = settings(scope="except", picks=["shop/db"])
    assert not up.wanted(s, "shop", "db") and up.wanted(s, "shop", "web")


def test_attached_follows_chains():
    svc = {"vpn": {}, "browser": {"network_mode": "service:vpn"}, "cdp": {"network_mode": "service:browser"}, "other": {"network_mode": "bridge"}}
    assert up.attached(svc, ["vpn"]) == ["browser", "cdp"]
    assert up.attached(svc, ["other"]) == []


# ---------- Suchen ----------
async def test_check_sorts_containers(server, stack, remote, fake_docker):  # noqa: F811
    remote["acme/web:1"] = NEW
    remote["library/postgres:16"] = registry.RegistryError("Registry bremst gerade")
    assert (await server.post("/api/v1/updates/check", json={})).json() == {"found": 1}
    rows = (await server.get("/api/v1/updates")).json()["results"]
    web = row(rows, "shop/web")
    assert (web["status"], web["remote"], web["can"], web["auto"]) == ("update", NEW, True, True)
    assert row(rows, "shop/vpn")["status"] == "current"
    assert row(rows, "shop/db")["status"] == "error" and "bremst" in row(rows, "shop/db")["message"]


async def test_built_image_is_never_looked_up(server, stack, remote):  # noqa: F811
    """Hieße ein lokal gebautes Image zufällig wie ein fremdes in der Registry, würde es sonst ersetzt."""
    remote["library/nora:local"] = NEW
    await server.post("/api/v1/updates/check", json={})
    r = row((await server.get("/api/v1/updates")).json()["results"], "shop/app")
    assert r["status"] == "local" and not r["can"]


async def test_short_unknown_name_counts_as_local(server, stack, remote):  # noqa: F811
    remote["library/postgres:16"] = registry.NotThere("Kein Zugriff")
    await server.post("/api/v1/updates/check", json={})
    assert row((await server.get("/api/v1/updates")).json()["results"], "shop/db")["status"] == "local"


async def test_moved_tag_counts_as_update(server, stack, remote, fake_docker):  # noqa: F811
    """Tag weitergerückt: Docker nennt nur die ID, das laufende Image hat keinen Digest mehr. Auch wenn die Registry nichts Neues hat."""
    c = fake_docker.containers[0]
    c["Image"], c["ConfigImage"], c["ImageID"] = "sha256:" + "c" * 64, "acme/web:1", "sha256:alt"
    fake_docker.images["sha256:alt"] = {"Id": "sha256:alt", "RepoDigests": []}
    fake_docker.images["acme/web:1"] = {"Id": "sha256:neu", "RepoDigests": [f"x@{OLD}"]}
    await server.post("/api/v1/updates/check", json={})
    r = row((await server.get("/api/v1/updates")).json()["results"], "shop/web")
    assert r["image"] == "acme/web:1" and r["status"] == "update" and r["message"]


async def test_stopped_and_plain_containers(server, stack, remote, fake_docker):  # noqa: F811
    fake_docker.containers[1]["State"] = "exited"
    plain = ctr("9", "solo", image="acme/solo:1")
    plain["ImageID"] = "sha256:solo"
    fake_docker.images["sha256:solo"] = {"Id": "sha256:solo", "RepoDigests": [f"x@{OLD}"]}
    fake_docker.containers.append(plain)
    remote["acme/solo:1"] = NEW
    await server.post("/api/v1/updates/check", json={})
    rows = (await server.get("/api/v1/updates")).json()["results"]
    assert "shop/db" not in {r["key"] for r in rows}    # ein Update würde ihn starten
    solo = row(rows, "solo/solo")
    assert solo["status"] == "update" and not solo["can"] and "ohne Compose" in solo["why"]
    assert (await server.post("/api/v1/updates/apply", json={"keys": ["solo/solo"]})).status_code == 400


# ---------- Aktualisieren ----------
async def test_update_touches_only_chosen_services(server, stack, remote, cmds, fake_docker):  # noqa: F811
    remote["acme/web:1"] = remote["library/postgres:16"] = NEW
    await server.post("/api/v1/updates/check", json={})
    assert (await server.post("/api/v1/updates/apply", json={"keys": ["shop/web"]})).status_code == 200
    await wait(server.app)
    pull, = cmds.verbs("pull")
    upc, = [a for a in cmds.verbs("up") if "--force-recreate" not in a]
    assert pull[-2:] == ["pull", "web"] and "db" not in pull
    assert "--no-deps" in upc and upc[-1] == "web" and "db" not in upc   # ohne --no-deps käme der ausgenommene Dienst mit
    h = (await server.get("/api/v1/updates")).json()["history"][0]
    assert h["ok"] and h["updated"] == ["shop/web"] and not h["auto"] and "log" not in h


async def test_old_image_is_removed_only_when_free(server, stack, remote, cmds, fake_docker, monkeypatch):  # noqa: F811
    remote["acme/web:1"] = NEW
    await server.post("/api/v1/updates/check", json={})

    async def swapped(_job, args, cwd=None, env=None):
        if "up" in args:
            fake_docker.containers[0]["ImageID"] = "sha256:web-neu"
        return True
    monkeypatch.setattr(up, "run_cmd", swapped)
    await server.post("/api/v1/updates/apply", json={})
    await wait(server.app)
    assert ("rmi", "sha256:web") in fake_docker.actions


async def test_attached_service_is_recreated_too(server, stack, remote, cmds):  # noqa: F811
    remote["acme/vpn:1"] = NEW
    await server.post("/api/v1/updates/check", json={})
    await server.post("/api/v1/updates/apply", json={"keys": ["shop/vpn"]})
    await wait(server.app)
    again = [a for a in cmds.verbs("up") if "--force-recreate" in a]
    assert len(again) == 1 and again[0][-1] == "browser"


async def test_busy_hook_postpones_and_nothing_is_pulled(server, stack, remote, cmds, monkeypatch, fake_docker):  # noqa: F811
    """Stella telefoniert (Exit 75): kein Pull, kein Neustart, die Automatik versucht es später."""
    fake_docker.containers[0]["Labels"].update(HOOK)
    remote["acme/web:1"] = NEW

    class Proc:
        returncode = up.BUSY_CODE

        async def communicate(self):
            return b"Stella telefoniert gerade", b""

    async def fake_exec(*args, **_kw):
        assert args[:2] == ("docker", "exec") and args[-1] == HOOK[up.HOOK]
        return Proc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    db = up.data(server.app)
    db.settings.update(action="now")
    await up.check(server.app)
    up.start_run(server.app, up.pending(db, auto=True), auto=True)
    await wait(server.app)
    assert not cmds.log
    assert db.state["history"][0]["later"] == ["shop/web"] and db.state["retry_at"] > time.time()


async def test_failed_update_is_not_retried_until_new_version(server, stack, remote, cmds):  # noqa: F811
    remote["acme/web:1"] = NEW
    cmds.fails.append(["up"])
    db = up.data(server.app)
    await up.check(server.app)
    assert [r["key"] for r in up.pending(db, auto=True)] == ["shop/web"]
    up.start_run(server.app, up.pending(db, auto=True), auto=True)
    await wait(server.app)
    assert not db.state["history"][0]["ok"] and db.state["history"][0]["failed"] == ["shop/web"]
    assert up.pending(db, auto=True) == []
    assert up.pending(db)                       # von Hand geht es weiter
    remote["acme/web:1"] = "sha256:" + "d" * 64
    await up.check(server.app)
    assert up.pending(db, auto=True)


async def test_unhealthy_container_is_reported_and_keeps_old_image(server, stack, remote, cmds, fake_docker):  # noqa: F811
    remote["acme/web:1"] = NEW
    await server.post("/api/v1/updates/check", json={})
    fake_docker.containers[0]["Status"] = "Up 1 minute (unhealthy)"
    await server.post("/api/v1/updates/apply", json={"keys": ["shop/web"]})
    await wait(server.app)
    h = (await server.get("/api/v1/updates")).json()["history"][0]
    assert not h["ok"] and h["problem"] == ["shop/web"] and h["updated"] == []
    assert not any(a[0] == "rmi" for a in fake_docker.actions)


async def test_server_updates_itself_through_helper(server, stack, remote, cmds, monkeypatch, fake_docker):  # noqa: F811
    me = fake_docker.containers[0]
    me["Id"] = "f" * 12
    monkeypatch.setenv("HOSTNAME", "f" * 12)
    remote["acme/web:1"] = remote["library/postgres:16"] = NEW
    db = up.data(server.app)
    await up.check(server.app)
    await server.post("/api/v1/updates/apply", json={})
    await wait(server.app)
    ups = [a for a in cmds.log if a[:2] == ["docker", "compose"] and "up" in a]
    assert ups and all("web" not in a for a in ups)    # direkt nur die anderen
    helper = cmds.log[-1]
    assert helper[:2] == ["docker", "run"] and "--rm" in helper and helper[helper.index("--entrypoint") + 2] == "acme/web:1"
    assert "up -d --no-deps --pull never web" in helper[-1]
    assert db.state["self_pending"]["key"] == "shop/web" and db.state["history"][0]["running"]
    # neuer Container auf neuem Image: Neustart hat geklappt
    me["ImageID"] = "sha256:web-neu"
    await up.settle_self(server.app)
    h = db.state["history"][0]
    assert db.state["self_pending"] is None and not h["running"] and "shop/web" in h["updated"]


async def test_failed_self_restart_is_reported(server, stack, remote, cmds, monkeypatch, fake_docker):  # noqa: F811
    fake_docker.containers[0]["Id"] = "f" * 12
    monkeypatch.setenv("HOSTNAME", "f" * 12)
    remote["acme/web:1"] = NEW
    db = up.data(server.app)
    await up.check(server.app)
    up.start_run(server.app, up.pending(db), auto=False)
    await wait(server.app)
    db.state["self_pending"]["at"] = time.time() - up.SELF_WAIT - 1
    await up.settle_self(server.app)
    assert db.state["self_pending"] is None and db.state["history"][0]["failed"] == ["shop/web"]


# ---------- Automatik ----------
async def run_tick(app, now):
    await up.tick(app, now)
    await wait(app)


async def test_default_only_shows(server, stack, remote, cmds):  # noqa: F811
    remote["acme/web:1"] = NEW
    await run_tick(server.app, at(5, 3))
    db = up.data(server.app)
    assert db.state["last_check"] and row(db.state["results"], "shop/web")["status"] == "update"
    assert not cmds.log


async def test_now_updates_after_search_except_excluded(server, stack, remote, cmds):  # noqa: F811
    remote["acme/web:1"] = remote["library/postgres:16"] = NEW
    db = up.data(server.app)
    db.settings.update(action="now", scope="except", picks=["shop/db"])
    await run_tick(server.app, at(5, 3))
    pull, = cmds.verbs("pull")
    assert pull[-1] == "web" and "db" not in pull


async def test_fixed_time_updates_only_then(server, stack, remote, cmds):  # noqa: F811
    remote["acme/web:1"] = NEW
    db = up.data(server.app)
    db.settings.update(action="at", apply_at="03:00", every_hours=1)
    await run_tick(server.app, at(5, 15))
    assert not cmds.log and row(db.state["results"], "shop/web")["status"] == "update"
    db.state["last_check"] = at(6, 3)
    await run_tick(server.app, at(6, 3, 1))
    assert cmds.verbs("pull") and db.state["last_apply"] == at(6, 3, 1)
    assert db.state["history"][0]["auto"]


async def test_only_list(server, stack, remote, cmds):  # noqa: F811
    remote["acme/web:1"] = remote["library/postgres:16"] = NEW
    up.data(server.app).settings.update(action="now", scope="only", picks=["shop/db"])
    await run_tick(server.app, at(5, 3))
    assert cmds.verbs("pull")[0][-1] == "db"


async def test_settings_are_checked_and_kept(server, stack, settings):  # noqa: F811
    bad = {"check": "daily", "every_hours": 6, "check_at": "25:00", "action": "at", "apply_at": "03:00", "scope": "all", "picks": []}
    assert (await server.put("/api/v1/updates/settings", json=bad)).status_code == 422
    empty = {**bad, "check_at": "03:00", "scope": "only"}
    assert (await server.put("/api/v1/updates/settings", json=empty)).status_code == 400
    ok = {**empty, "picks": ["shop"]}
    assert (await server.put("/api/v1/updates/settings", json=ok)).status_code == 200
    assert up.Data(settings.data_dir / "updates.json").settings["picks"] == ["shop"]
    assert (await server.get("/api/v1/updates")).json()["stacks"][0] == {"name": "shop", "services": ["app", "browser", "db", "vpn", "web"]}


async def test_log_and_watchtower_hint(server, stack, remote, cmds, fake_docker):  # noqa: F811
    remote["acme/web:1"] = NEW
    await server.post("/api/v1/updates/check", json={})
    await server.post("/api/v1/updates/apply", json={"keys": ["shop/web"]})
    await wait(server.app)
    h = (await server.get("/api/v1/updates")).json()
    assert not h["watchtower"]
    assert (await server.get(f"/api/v1/updates/log/{h['history'][0]['id']}")).status_code == 200
    assert (await server.get("/api/v1/updates/log/gibtsnicht")).status_code == 404
    fake_docker.containers.append(ctr("8", "wt", image="containrrr/watchtower"))
    assert (await server.get("/api/v1/updates")).json()["watchtower"]


async def test_stack_actions_wait_for_running_update(server, stack, remote, cmds, monkeypatch):  # noqa: F811
    gate = asyncio.Event()

    async def held(_job, _args, cwd=None, env=None):
        await gate.wait()
        return True
    monkeypatch.setattr(up, "run_cmd", held)
    remote["acme/web:1"] = NEW
    await server.post("/api/v1/updates/check", json={})
    await server.post("/api/v1/updates/apply", json={"keys": ["shop/web"]})
    assert (await server.post("/api/v1/stacks/shop/action", json={"action": "restart"})).status_code == 409
    assert (await server.post("/api/v1/updates/apply", json={})).status_code == 409
    gate.set()
    await wait(server.app)


def test_compose_gets_no_foreign_environment(monkeypatch):
    """Variablen der Umgebung gehen in Compose vor .env: Unser Token landete sonst in Stacks mit gleichnamiger Variable."""
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "server-token")
    monkeypatch.setenv("DATA_DIR", "/data")
    env = stacks_mod.docker_env()
    assert "CLOUDFLARE_API_TOKEN" not in env and "DATA_DIR" not in env and "PATH" in env
