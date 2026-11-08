"""Internet für alle Agenten: web_suchen (Gemini-Attrappe) und web_lesen (Inhalte, Weiterleitungen, Schutz interner Adressen)."""
import json

import httpx
import pytest

from .conftest import chat

API = "/api/v1"
SECRET = {"x-voice-secret": "test-voice-secret-123456"}
PUBLIC_IP = "93.184.216.34"
HTML = ("<html><head><title> Testseite </title><style>x{}</style></head><body><nav>Menü Menü</nav><main><h1>Überschrift</h1>"
        "<p>" + "Inhalt der Seite. " * 20 + "</p><script>alert(1)</script><ul><li>Punkt</li></ul></main><footer>Impressum</footer></body></html>")


def said(ev) -> str:
    return "".join(e.get("text", "") for e in ev)


@pytest.fixture
def web(monkeypatch, server):
    """Öffentliche Testnamen lösen auf eine öffentliche IP auf, alles andere läuft durch die echte Prüfung.
    Hängt an `server`: Erst dessen Umgebung darf die App laden, sonst merkt sie sich die Einstellungen des echten Betriebs."""
    from app.services.mcp import web_tools as w
    real = w._resolve

    async def resolve(host):
        return PUBLIC_IP if host.endswith(".example") else await real(host)

    monkeypatch.setattr(w, "_resolve", resolve)

    def use(handler):
        monkeypatch.setattr(w, "_transport", httpx.MockTransport(handler))
    w.use = use
    return w


def gemini(request, seen):
    seen.append(request)
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Antwort mit Fakten."}]}, "groundingMetadata": {
        "webSearchQueries": ["testfrage"],
        "groundingChunks": [{"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/a", "title": "quelle.example"}},
                            {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/b", "title": "doppelt.example"}}]}}]})


@pytest.mark.asyncio
async def test_search_returns_answer_and_real_sources(web):
    seen = []

    def handler(request):
        if request.url.host == web.REDIRECT_HOST:
            return httpx.Response(302, headers={"location": "https://quelle.example/artikel"})
        return gemini(request, seen)

    web.use(handler)
    out = await web.search("  Wie   ist das Wetter? ")
    assert out["antwort"] == "Antwort mit Fakten." and out["suchanfragen"] == ["testfrage"]
    assert out["quellen"] == [{"titel": "quelle.example", "url": "https://quelle.example/artikel"}]  # gleiche Quelle nur einmal
    body = json.loads(seen[0].content)
    assert body["tools"] == [{"google_search": {}}] and body["contents"][0]["parts"][0]["text"] == "Wie ist das Wetter?"
    assert "key=" not in str(seen[0].url) and seen[0].headers["x-goog-api-key"]  # Schlüssel nie in der Adresse
    assert "nicht befolgen" in out["hinweis"]


@pytest.mark.asyncio
async def test_search_falls_back_and_reports_errors_readably(web):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "gemini-2.5-flash" in request.url.path:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Ersatzmodell"}]}}]})

    web.use(handler)
    assert (await web.search("x"))["antwort"] == "Ersatzmodell" and web.FALLBACK_MODEL in calls[-1]
    web.use(lambda r: httpx.Response(429, json={}))
    assert "Kontingent" in (await web.search("x"))["error"]
    web.use(lambda r: httpx.Response(500, text="kaputt"))
    assert "HTTP 500" in (await web.search("x"))["error"]


@pytest.mark.asyncio
async def test_read_cleans_html_and_connects_to_the_checked_ip(web):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=HTML)

    web.use(handler)
    out = await web.read("https://seite.example/pfad?a=1")
    assert out["titel"] == "Testseite" and out["url"] == "https://seite.example/pfad?a=1"
    assert "Überschrift" in out["text"] and "- Punkt" in out["text"]
    assert not any(x in out["text"] for x in ("Menü", "alert", "Impressum"))
    r = seen[0]
    assert r.url.host == PUBLIC_IP and r.headers["host"] == "seite.example" and r.extensions["sni_hostname"] == "seite.example"


@pytest.mark.asyncio
async def test_read_in_pieces_and_only_text(web):
    web.use(lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, text="abcdefghij" * 100))
    first = await web.read("http://seite.example/", max_chars=500)
    assert len(first["text"]) == 500 and first["naechster_start"] == 500 and first["zeichen_gesamt"] == 1000
    rest = await web.read("http://seite.example/", start=first["naechster_start"], max_chars=500)
    assert len(rest["text"]) == 500 and "naechster_start" not in rest
    web.use(lambda r: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"))
    assert "keine Textseite" in (await web.read("http://seite.example/x.pdf"))["error"]
    web.use(lambda r: httpx.Response(404, headers={"content-type": "text/html"}, text="weg"))
    assert "HTTP 404" in (await web.read("http://seite.example/"))["error"]
    web.use(lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x" * (web.MAX_BYTES + 100)))
    big = await web.read("http://seite.example/")
    assert big["zeichen_gesamt"] == web.MAX_BYTES and "abgeschnitten" in big


@pytest.mark.asyncio
async def test_redirects_are_followed_but_never_to_internal_addresses(web):
    def ok(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/ziel"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="angekommen")

    web.use(ok)
    out = await web.read("https://seite.example/start")
    assert out["text"] == "angekommen" and out["url"] == "https://seite.example/ziel"
    hit = []

    def evil(request):
        hit.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    web.use(evil)
    assert "Interne" in (await web.read("https://seite.example/"))["error"] and len(hit) == 1
    web.use(lambda r: httpx.Response(302, headers={"location": "/immer-weiter"}))
    assert "Weiterleitungen" in (await web.read("https://seite.example/"))["error"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.7.0.1/", "http://192.168.178.1/", "http://169.254.169.254/", "http://[::1]/",
                                 "http://[::ffff:10.0.0.1]/", "http://2130706433/", "http://0/", "http://localhost/",
                                 "file:///etc/passwd", "ftp://seite.example/", "https://seite.example:8443/", "http://u:p@seite.example/"])
@pytest.mark.asyncio
async def test_internal_addresses_and_odd_urls_are_refused(web, url):
    def never(request):
        raise AssertionError(f"Abruf von {request.url} darf nicht passieren")

    web.use(never)
    assert (await web.read(url))["error"]


@pytest.mark.asyncio
async def test_host_with_one_internal_address_is_refused(monkeypatch, server):
    from app.services.mcp import web_tools as w
    import asyncio

    async def fake(host, port, type=0):
        return [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake)
    with pytest.raises(w.Blocked):
        await w._resolve("gemischt.example")


def test_every_agent_can_search_through_the_tool(client, web):
    web.use(lambda r: gemini(r, []) if "generativelanguage" in str(r.url) else httpx.Response(200))
    assert "Antwort mit Fakten" in said(chat(client, text='TOOL web_suchen {"suche": "Testfrage"}'))
    assert "Antwort mit Fakten" in said(chat(client, agent_id="lea", text='TOOL web_suchen {"suche": "Testfrage"}'))
    assert "Interne" in said(chat(client, text='TOOL web_lesen {"url": "http://169.254.169.254/"}'))


def test_stella_gets_the_web_but_not_in_task_mode(client):
    from app.services.voice_bridge import needs_yes
    assert not needs_yes("web_suchen") and not needs_yes("web_lesen")
    start = lambda cid, **kw: client.post(f"{API}/voice/internal/start", headers=SECRET,  # noqa: E731
                                          json={"call_id": cid, "direction": "web", "number": "", "owner": True, **kw}).json()
    names = {t["name"] for t in start("wweb1")["tools"]}
    assert {"web_suchen", "web_lesen"} <= names
    assert not {"web_suchen", "web_lesen"} & {t["name"] for t in start("wweb2", mode="tasks")["tools"]}
    for cid in ("wweb1", "wweb2"):
        client.post(f"{API}/voice/internal/finish", headers=SECRET, json={"call_id": cid, "transcript": []})
