"""Weiterleitungen: Verwaltung im Server, Regeln und öffentlicher Dienst."""
import httpx
import pytest

from app import links_store as ls
from app.config import get_settings
from app.links_public import Guard, create_links_app

from .conftest import ctr
from .test_server import fake_cf, server  # noqa: F401

BROWSER = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1", "cf-connecting-ip": "1.2.3.4"}
DOMAINS = "l.example.com,link.example.com,l.example.org"


@pytest.fixture
def domains(settings, monkeypatch):
    monkeypatch.setenv("LINKS_DOMAINS", DOMAINS)
    get_settings.cache_clear()


@pytest.fixture
async def public(domains):
    app = create_links_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://l.example.com", headers=BROWSER) as c:
        c.store = app.state.links
        yield c
    app.state.links.close()


def add(store, **kw):
    return store.create({"code": kw.pop("code", "abc2345"), "target": "https://ziel.example.net/x", "domain": "l.example.com", **kw})


def test_codes_are_short_and_unambiguous(tmp_path):
    db = ls.Store(tmp_path / "l.db")
    codes = {db.new_code() for _ in range(200)}
    assert len(codes) == 200 and all(len(c) == ls.CODE_LEN and set(c) <= set(ls.ALPHABET) for c in codes)
    assert not set("l1io0") & set(ls.ALPHABET)
    db.close()


async def test_code_works_on_every_domain_any_case(public):
    add(public.store)
    for host in DOMAINS.split(","):
        r = await public.get(f"https://{host}/ABC2345")
        assert r.status_code == 302 and r.headers["location"] == "https://ziel.example.net/x"
        assert r.headers["cache-control"].startswith("no-store")
    assert public.store.by_code("abc2345")["uses"] == 3


async def test_single_use_and_max_uses(public):
    add(public.store, max_uses=2)
    assert (await public.get("/abc2345")).status_code == 302
    assert (await public.get("/abc2345")).status_code == 302
    r = await public.get("/abc2345")
    assert r.status_code == 410 and "ziel.example.net" not in r.text


async def test_time_rules():
    link = {"enabled": 1, "created": 1000, "uses": 0, "device_mode": "any", "hours": 1, "hours_from": "create"}
    assert ls.check(link, "d", False, 1000 + 3599) == "" and ls.check(link, "d", False, 1000 + 3600) == "expired"
    first = {**link, "hours_from": "first", "first_open": None}
    assert ls.check(first, "d", False, 999999) == ""  # läuft erst ab dem ersten Öffnen
    assert ls.check({**first, "first_open": 5000}, "d", False, 5000 + 3600) == "expired"
    span = {**link, "hours": None, "valid_from": 2000, "valid_until": 3000}
    assert [ls.check(span, "d", False, t) for t in (1999, 2500, 3000)] == ["not_yet", "", "expired"]


async def test_once_per_device(public):
    add(public.store, device_mode="once")
    assert (await public.get("/abc2345")).status_code == 302
    assert (await public.get("/abc2345")).status_code == 403  # gleiches Cookie
    public.cookies.clear()
    assert (await public.get("/abc2345")).status_code == 302  # anderes Gerät


async def test_bind_to_first_device(public):
    add(public.store, device_mode="bind")
    assert (await public.get("/abc2345")).status_code == 302
    assert (await public.get("/abc2345")).status_code == 302
    mine = dict(public.cookies)
    public.cookies.clear()
    assert (await public.get("/abc2345")).status_code == 403
    public.cookies.clear()
    public.cookies.update(mine)
    assert (await public.get("/abc2345")).status_code == 302


async def test_device_rules_hop_to_home_domain(public):
    """Cookie gilt je Domain: sonst wäre dasselbe Gerät auf l.… und link.… zwei Geräte."""
    add(public.store, device_mode="bind")
    r = await public.get("https://link.example.com/abc2345")
    assert r.status_code == 307 and r.headers["location"] == "https://l.example.com/abc2345"
    assert public.store.by_code("abc2345")["uses"] == 0
    assert (await public.get(r.headers["location"])).status_code == 302
    assert (await public.get("https://l.example.org/abc2345")).status_code == 307


@pytest.mark.parametrize("agent", ["WhatsApp/2.23", "TelegramBot (like TwitterBot)", "facebookexternalhit/1.1", "Slackbot-LinkExpanding 1.0"])
async def test_previews_do_not_use_up_the_link(public, agent):
    add(public.store, max_uses=1)
    r = await public.get("/abc2345", headers={"user-agent": agent})
    assert r.status_code == 200 and "ziel.example.net" not in r.text
    assert public.store.by_code("abc2345")["uses"] == 0
    assert (await public.head("/abc2345")).status_code == 200
    assert (await public.get("/abc2345")).status_code == 302


async def test_confirm_page_counts_only_on_click(public):
    add(public.store, confirm=1, max_uses=1)
    r = await public.get("/abc2345")
    assert r.status_code == 200 and 'method="post"' in r.text and "ziel.example.net" not in r.text
    assert public.store.by_code("abc2345")["uses"] == 0
    assert (await public.post("/abc2345")).status_code == 302


async def test_guessing_gets_a_pause(public):
    add(public.store)
    for i in range(20):
        assert (await public.get(f"/falsch{i}")).status_code == 404
    assert (await public.get("/abc2345")).status_code == 429
    other = {**BROWSER, "cf-connecting-ip": "5.6.7.8"}
    assert (await public.get("/abc2345", headers=other)).status_code == 302  # andere IP ist nicht betroffen


def test_guard_forgets_old_misses():
    g = Guard(limit=2, window=10)
    g.miss("a", 100), g.miss("a", 101)
    assert g.blocked("a", 105) and not g.blocked("a", 111)


async def test_public_service_has_no_admin_api(public):
    assert (await public.get("/api/v1/links")).status_code == 404
    assert (await public.post("/api/v1/links", json={"target": "https://x.de"})).status_code in (404, 405)


async def test_manage_links(domains, server):
    r = await server.post("/api/v1/links", json={"target": "example.net/seite", "max_uses": 1, "note": "Test"})
    assert r.status_code == 200
    d = r.json()
    assert d["target"] == "https://example.net/seite" and d["status"] == "active" and len(d["code"]) == ls.CODE_LEN
    assert d["url"] == f"https://l.example.com/{d['code']}" and len(d["urls"]) == 3 and "bound_device" not in d

    r = await server.put(f"/api/v1/links/{d['id']}", json={"target": "https://neu.example.net", "code": "Mein-Link", "domain": "l.example.org"})
    assert r.json()["code"] == "mein-link" and r.json()["url"] == "https://l.example.org/mein-link"

    db = server.app.state.links
    db.open("mein-link", "geraet-geheim-123456", agent="x")
    d2 = (await server.get(f"/api/v1/links/{d['id']}")).json()
    assert d2["uses"] == 1 and d2["devices"] == 1 and len(d2["visits"]) == 1 and "geraet-geheim" not in str(d2)

    assert (await server.post(f"/api/v1/links/{d['id']}/enabled", json={"enabled": False})).json()["status"] == "disabled"
    assert (await server.post(f"/api/v1/links/{d['id']}/reset", json={})).json()["uses"] == 0
    assert (await server.delete(f"/api/v1/links/{d['id']}", headers={"content-type": "application/json"})).status_code == 200
    assert (await server.get("/api/v1/links")).json()["links"] == []


@pytest.mark.parametrize("body", [
    {"target": "javascript:alert(1)"}, {"target": "ftp://example.net/a"}, {"target": "https://exa mple.net"},
    {"target": "https://l.example.com/abc"},  # Schleife
    {"target": "https://a.net", "code": "api"}, {"target": "https://a.net", "code": "a b"}, {"target": "https://a.net", "domain": "fremd.de"},
    {"target": "https://a.net", "valid_from": 200, "valid_until": 100},
])
async def test_bad_input(domains, server, body):
    assert (await server.post("/api/v1/links", json=body)).status_code == 400


async def test_taken_code(domains, server):
    body = {"target": "https://a.net", "code": "lebenslauf"}
    assert (await server.post("/api/v1/links", json=body)).status_code == 200
    assert (await server.post("/api/v1/links", json=body)).status_code == 409


async def test_setup_domains(server, fake_cf, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LINKS_DOMAINS", "l.example.com,link.example.com")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    get_settings.cache_clear()
    fake_cf.ingress = [{"hostname": "link.example.com", "service": "http://10.7.0.1:9999"}, {"service": "http_status:404"}]
    c = ctr("l", "links", "ida", service="links", labels={"ida.links": "public"})
    c["Ports"] = [{"PrivatePort": 8000, "PublicPort": 8089, "Type": "tcp", "IP": "10.7.0.1"}]
    server.docker.containers = [c]

    d = (await server.get("/api/v1/links")).json()
    assert d["upstream"] == "http://10.7.0.1:8089" and [x["ready"] for x in d["domains"]] == [False, False]

    r = (await server.post("/api/v1/links/setup", json={})).json()
    assert not r["ok"] and [s["ok"] for s in r["steps"]] == [True, False]  # fremde Regel bleibt stehen
    assert {"hostname": "link.example.com", "service": "http://10.7.0.1:9999"} in fake_cf.ingress
    assert {"hostname": "l.example.com", "service": "http://10.7.0.1:8089"} in fake_cf.ingress
    assert [x["name"] for x in fake_cf.dns] == ["l.example.com"]
    assert not fake_cf.apps  # bewusst ohne Zero Trust
    d = (await server.get("/api/v1/links")).json()
    assert [x["ready"] for x in d["domains"]] == [True, False]
