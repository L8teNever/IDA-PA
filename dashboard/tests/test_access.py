import httpx
import pytest

from app import access


def test_classify():
    assert access.classify(302, {"Location": "https://team.cloudflareaccess.com/cdn-cgi/access/login/x"})["state"] == "access"
    assert access.classify(302, {"location": "/cdn-cgi/access/login/x"})["state"] == "access"
    assert access.classify(403, {"cf-access-domain": "x.example.com"})["state"] == "access"
    assert access.classify(403, {}, "<title>Cloudflare Access</title>")["state"] == "access"
    assert access.classify(403, {}, "verboten")["state"] == "public"  # Ablehnung der App selbst
    assert access.classify(302, {"Location": "/login"})["state"] == "public"  # eigener Login der App, kein Access
    assert access.classify(200, {})["state"] == "public"
    assert access.classify(403, {"cf-mitigated": "challenge"})["state"] == "unknown"
    assert access.classify(502, {})["state"] == "unknown"


@pytest.mark.asyncio
async def test_check_caches_and_handles_errors(monkeypatch):
    access.reset()
    calls = []

    async def boom(url):
        calls.append(url)
        raise httpx.ConnectError("weg")

    monkeypatch.setattr(access, "fetch", boom)
    assert (await access.check("https://a.example.com"))["state"] == "unknown"
    assert (await access.check("https://a.example.com"))["state"] == "unknown"
    assert calls == ["https://a.example.com"]
    access.reset()


APPS = [
    {"name": "Dashboard", "type": "self_hosted", "domains": ["dashboard.example.com"], "policies": [{"name": "Nur Simon", "decision": "allow"}]},
    {"name": "Alle Subdomains", "type": "self_hosted", "domains": ["*.intern.example.com"], "policies": [{"name": "Team", "decision": "allow"}]},
    {"name": "Admin", "type": "self_hosted", "domains": ["shop.example.com/admin"], "policies": [{"name": "Admins", "decision": "allow"}]},
    {"name": "Webhook offen", "type": "self_hosted", "domains": ["dashboard.example.com/api/hook"], "policies": [{"name": "Alle", "decision": "bypass"}]},
]


def test_judge_from_access_apps():
    from app.cloudflare_api import judge
    assert judge("https://dashboard.example.com", APPS)["state"] == "access"
    assert "Nur Simon" in judge("https://dashboard.example.com", APPS)["detail"]
    assert judge("https://wiki.intern.example.com", APPS)["state"] == "access"  # Wildcard
    r = judge("https://shop.example.com", APPS)
    assert r["state"] == "public" and "/admin" in r["detail"]  # nur ein Pfad geschützt
    assert judge("https://shop.example.com/admin/users", APPS)["state"] == "access"
    assert judge("https://dashboard.example.com/api/hook", APPS)["state"] == "public"  # Bypass, längerer Treffer gewinnt
    assert judge("https://frei.example.com", APPS)["state"] == "public"


def test_app_view_reads_domains_and_destinations():
    from app.cloudflare_api import _app_view
    v = _app_view({"id": "1", "name": "X", "type": "self_hosted", "domain": "A.example.com",
                   "self_hosted_domains": ["a.example.com", "b.example.com"],
                   "destinations": [{"type": "public", "uri": "c.example.com/x"}, {"type": "private", "cidr": "10.0.0.0/8"}],
                   "policies": [{"name": "P", "decision": "allow", "include": []}]})
    assert v["domains"] == ["a.example.com", "b.example.com", "c.example.com/x"] and v["policies"] == [{"name": "P", "decision": "allow"}]


async def test_annotate_prefers_api_and_falls_back_to_probe(monkeypatch):
    from app import cloudflare_api

    async def apps():
        return APPS

    probed = []

    async def fetch(url):
        probed.append(url)
        return 302, {"location": "https://t.cloudflareaccess.com/cdn-cgi/access/login"}, ""

    monkeypatch.setattr(access, "fetch", fetch)
    monkeypatch.setattr(cloudflare_api, "enabled", lambda: True)
    monkeypatch.setattr(cloudflare_api, "access_apps", apps)
    access.reset()
    urls = {"c": [{"url": "https://shop.example.com"}, {"url": "https://dashboard.example.com"}]}
    out = await access.annotate(urls)
    assert [(u["access"], u["access_source"]) for u in out["c"]] == [("public", "api"), ("access", "api")] and not probed

    async def broken():
        raise cloudflare_api.CloudflareError("kein Recht")

    monkeypatch.setattr(cloudflare_api, "access_apps", broken)
    out = await access.annotate({"c": [{"url": "https://shop.example.com"}]})
    assert out["c"][0]["access"] == "access" and out["c"][0]["access_source"] == "probe" and probed == ["https://shop.example.com"]
    access.reset()


async def test_cloudflare_api_pages_account_and_tunnels(monkeypatch, settings):
    import httpx
    from app import cloudflare_api

    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.url.path, dict(req.url.params)))
        assert req.headers["authorization"] == "Bearer tok"
        p = req.url.path.replace("/client/v4", "")
        if p == "/accounts":
            return httpx.Response(200, json={"success": True, "result": [{"id": "acc1"}]})
        if p == "/accounts/acc1/access/apps":
            page = int(req.url.params["page"])
            return httpx.Response(200, json={"success": True, "result": [{"name": f"App{page}", "type": "self_hosted", "domain": f"a{page}.example.com"}],
                                             "result_info": {"total_pages": 2}})
        if p == "/accounts/acc1/cfd_tunnel":
            return httpx.Response(200, json={"success": True, "result": [{"id": "t1", "name": "main", "remote_config": True}, {"id": "t2", "remote_config": False}]})
        if p == "/accounts/acc1/cfd_tunnel/t1/configurations":
            return httpx.Response(200, json={"success": True, "result": {"config": {"ingress": [{"hostname": "shop.example.com", "service": "http://web:80"}, {"service": "http_status:404"}]}}})
        return httpx.Response(403, json={"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(cloudflare_api.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    settings.cloudflare_api_token = "tok"
    settings.cloudflare_api_url = "https://cf.test/client/v4"
    cloudflare_api.reset()
    assert [a["name"] for a in await cloudflare_api.access_apps()] == ["App1", "App2"]
    assert await cloudflare_api.tunnel_routes() == [{"host": "shop.example.com", "service": "http://web:80", "path": None, "tunnel": "main"}]
    st = await cloudflare_api.status()
    assert st == {"enabled": True, "account_id": "acc1", "access_apps": 2, "tunnel_routes": 1}
    settings.cloudflare_account_id = "andere"
    cloudflare_api.reset()
    st = await cloudflare_api.status()
    assert st["error"].startswith("[10000]")
    cloudflare_api.reset()
