"""Tests für das Sicherheits- & Rechtemodell (`privat/`, `memory/`, `ki/`), Freigabe-Proposals & externes `/mcp`."""
import json

from conftest import API


def _mcp_rpc(mc, method: str, params: dict | None = None, req_id: int = 1):
    return mc.post("/mcp", json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})


def test_ida_agent_rights_and_private_proposal_flow(client, agent_client):
    ag = agent_client("main")

    # 1. Agent darf `privat/` lesen, aber NIEMALS direkt schreiben (403 Forbidden)
    assert ag.get(f"{API}/page", params={"ref": "privat/_start"}).status_code == 200
    denied = ag.post(
        f"{API}/pages",
        json={"path": "privat/00-inbox/heimlich", "title": "Heimlich", "body": "Darf nicht klappen"},
    )
    assert denied.status_code == 403

    # 2. Agent darf auch `ki/` nicht direkt überschreiben
    assert ag.post(f"{API}/pages", json={"path": "ki/main/manipuliert", "title": "X", "body": "Y"}).status_code == 403

    # 3. Offizieller Proposal-Ablauf für `privat/`: Diff erzeugen -> Commit mit Geheimnis -> Revision trägt approved_by="user"
    prop_res = ag.post(
        f"{API}/internal/proposals",
        json={
            "agent_id": "main",
            "action": "create",
            "payload": {
                "path": "privat/00-inbox/reiseidee-lissabon",
                "title": "Reiseidee Lissabon",
                "body": "# Lissabon\n\nFlüge im Herbst prüfen.\n",
                "summary": "Neue Reiseidee in der Inbox anlegen",
            },
        },
    )
    assert prop_res.status_code == 200, prop_res.text
    prop = prop_res.json()["proposal"]
    secret = prop_res.json()["commit_secret"]
    assert "+# Lissabon" in prop["diff_preview"]

    # Ohne korrektes commit_secret wird Commit abgelehnt
    bad_commit = ag.post(f"{API}/internal/proposals/{prop['id']}/commit", json={"commit_secret": "falsch"})
    assert bad_commit.status_code == 403

    # Mit korrektem commit_secret (nach Klick auf „Erlauben“ in IDA) wird geschrieben
    ok_commit = ag.post(f"{API}/internal/proposals/{prop['id']}/commit", json={"commit_secret": secret})
    assert ok_commit.status_code == 200, ok_commit.text
    created = ok_commit.json()["page"]
    assert created["path"] == "privat/00-inbox/reiseidee-lissabon"

    revs = client.get(f"{API}/pages/{created['id']}/revisions").json()["revisions"]
    assert revs[0]["by"] == "main"
    assert revs[0]["approved_by"] == "user"

    # 4. Zweites Proposal kann auch direkt in der Wiki-Oberfläche vom Nutzer freigegeben werden
    prop2 = ag.post(
        f"{API}/internal/proposals",
        json={
            "agent_id": "researcher",
            "action": "patch",
            "payload": {
                "path": "privat/00-inbox/reiseidee-lissabon",
                "heading": "Hotels",
                "mode": "append",
                "new_text": "- Boutique-Hotel in Alfama ansehen",
            },
        },
    ).json()["proposal"]
    ui_approve = client.post(f"{API}/proposals/{prop2['id']}/approve")
    assert ui_approve.status_code == 200
    assert "Boutique-Hotel in Alfama" in ui_approve.json()["page"]["body"]


def test_memory_structure_guard_secret_scanner_and_prompt_context(client, agent_client):
    ag = agent_client("main")

    # 1. Lose Datei direkt unter `memory/` oder in Fantasie-Ordner wird strikt abgewiesen
    assert ag.post(
        f"{API}/pages",
        json={"path": "memory/lose-notiz", "title": "Lose", "body": "Text", "frontmatter": {"summary": "Kurz"}},
    ).status_code == 400
    assert ag.post(
        f"{API}/pages",
        json={"path": "memory/chaos-ordner/test", "title": "Chaos", "body": "Text", "frontmatter": {"summary": "Kurz"}},
    ).status_code == 400

    # 2. Geheimnisse (API-Keys / Tokens) werden blockiert
    sec = ag.post(
        f"{API}/pages",
        json={
            "path": "memory/projekte/geheim",
            "title": "API Zugänge",
            "body": "Mein Key ist sk-proj-1234567890abcdefghijklmnop",
            "frontmatter": {"summary": "Zugangsdaten"},
        },
    )
    assert sec.status_code == 400
    assert "Geheimnisse" in sec.json()["detail"]

    # 3. Saubere strukturierte Seite in `memory/projekte/ida-stack` anlegen
    ok = ag.post(
        f"{API}/pages",
        json={
            "path": "memory/projekte/ida-stack",
            "title": "IDA Server & Docker-Stack",
            "body": "# IDA Stack\n\nLäuft unter Docker Compose mit Kalender (8082) und Wiki (8083).\n",
            "frontmatter": {"summary": "Architektur des lokalen IDA Docker-Stacks", "tags": ["docker", "ida"], "importance": 5},
        },
    )
    assert ok.status_code == 200, ok.text

    # 4. Nahe Dublette unter neuem Pfad wird erkannt -> Agent soll bestehende Seite patchen!
    dup = ag.post(
        f"{API}/pages",
        json={
            "path": "memory/projekte/ida-docker-stack",
            "title": "IDA Server Docker-Stack",
            "body": "Noch eine Notiz.",
            "frontmatter": {"summary": "Doppelt"},
        },
    )
    assert dup.status_code == 400
    assert "wiki_memory_patch" in dup.json()["detail"]

    # 5. `memory/_index` wurde automatisch aktualisiert und enthält den neuen Link
    idx = client.get(f"{API}/page", params={"ref": "memory/_index"}).json()
    assert "[[memory/projekte/ida-stack|IDA Server & Docker-Stack]]" in idx["body"]

    # 6. System-Prompt-Gedächtniskontext liefert Kern-Wissen + passende Projektseite
    ctx = ag.get(f"{API}/memory/context", params={"agent_id": "main", "q": "Docker Wiki 8083"}).json()
    assert "memory/kern/ueber-mich" in ctx["text"]
    assert "memory/projekte/ida-stack" in ctx["text"]


def test_external_mcp_server_strict_read_only_for_privat_and_named_tokens(client, mcp_client, server_env):
    # 1. Ohne Token -> 401
    import httpx
    anon = httpx.post(f"{server_env['url']}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert anon.status_code == 401

    mc = mcp_client()
    init = _mcp_rpc(mc, "initialize")
    assert init.status_code == 200
    assert init.json()["result"]["protocolVersion"] == "2025-03-26"

    tools = _mcp_rpc(mc, "tools/list").json()["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    assert "wiki_memory_save" in tool_names
    assert "wiki_read" in tool_names
    # Keine `wiki_private_*`-Schreibtools im externen MCP!
    assert not any(n.startswith("wiki_private_") for n in tool_names)

    # 2. Externes MCP kann `privat/` lesen
    rd = _mcp_rpc(mc, "tools/call", {"name": "wiki_read", "arguments": {"path": "privat/_start"}}).json()["result"]
    assert rd["isError"] is False
    assert "Willkommen" in json.loads(rd["content"][0]["text"])["body"]

    # 3. Manipulationsversuch: externes MCP versucht `wiki_memory_patch` oder `wiki_memory_delete` auf `privat/` -> STRIKT BLOCKIERT!
    evil_patch = _mcp_rpc(
        mc,
        "tools/call",
        {"name": "wiki_memory_patch", "arguments": {"path": "privat/_start", "new_text": "Gehackt"}},
    ).json()["result"]
    assert evil_patch["isError"] is True

    evil_del = _mcp_rpc(
        mc,
        "tools/call",
        {"name": "wiki_memory_delete", "arguments": {"path": "privat/_start"}},
    ).json()["result"]
    assert evil_del["isError"] is True

    # 4. Externes MCP schreibt regulär ins strukturierte `memory/konzepte-wissen/`
    mem_save = _mcp_rpc(
        mc,
        "tools/call",
        {
            "name": "wiki_memory_save",
            "arguments": {
                "category": "konzepte-wissen",
                "slug": "cloudflare-zero-trust",
                "title": "Cloudflare Zero Trust Setup",
                "summary": "Wiki und Kalender laufen hinter Cloudflare Access ohne eigenen App-Login",
                "body": "## Aufbau\n\nAlle Subdomains unter `kulbarts.com` werden vorab authentifiziert.\n",
                "tags": ["cloudflare", "security"],
            },
        },
    ).json()["result"]
    assert mem_save["isError"] is False
    saved_rev = json.loads(mem_save["content"][0]["text"])["revision_id"]

    # 5. Benanntes Token OHNE `allow_read_private` darf `memory/` nutzen, aber NICHT `privat/` lesen!
    tok_res = client.post(
        f"{API}/mcp-tokens",
        json={"name": "Laptop Cursor", "allow_read_private": False, "allow_read_chats": False},
    )
    assert tok_res.status_code == 200
    named_raw = tok_res.json()["token"]
    named_mc = mcp_client(named_raw)

    priv_try = _mcp_rpc(named_mc, "tools/call", {"name": "wiki_read", "arguments": {"path": "privat/_start"}}).json()["result"]
    assert priv_try["isError"] is True

    mem_try = _mcp_rpc(named_mc, "tools/call", {"name": "wiki_read", "arguments": {"path": "memory/konzepte-wissen/cloudflare-zero-trust"}}).json()["result"]
    assert mem_try["isError"] is False

    # Undo durch denselben MCP-Aufrufer (`mc`) funktioniert
    undo_res = _mcp_rpc(mc, "tools/call", {"name": "wiki_memory_undo", "arguments": {"revision_id": saved_rev}}).json()["result"]
    assert undo_res["isError"] is False
