"""Tests für Lebensordner-Seeding, Markdown-Vault-Dateien, [[Wikilinks]], Backlinks, Move-Rewrite, Suche & Wissensgraph."""
from conftest import API


def test_seed_creates_spaces_and_life_folders(client, server_env):
    r = client.get(f"{API}/tree")
    assert r.status_code == 200, r.text
    tree = r.json()
    assert tree["spaces"] == ["privat", "memory", "ki"]

    # Alle 10 persönlichen Lebensordner vorhanden
    for f in (
        "privat/00-inbox",
        "privat/01-alltag-wohnen",
        "privat/02-finanzen-steuern",
        "privat/04-arbeit-projekte",
        "privat/99-archiv",
    ):
        assert f in tree["folders"], f

    paths = {p["path"] for p in tree["pages"]}
    assert "privat/_start" in paths
    assert "memory/_index" in paths
    assert "memory/_schema" in paths
    assert "memory/kern/ueber-mich" in paths
    assert "ki/_uebersicht" in paths

    # Echte .md-Dateien liegen physisch auf der Platte
    vault = server_env["data_dir"] / "vault"
    assert (vault / "privat" / "_start.md").is_file()
    assert (vault / "memory" / "_index.md").is_file()


def test_wikilinks_backlinks_graph_and_move_rewrite(client, server_env):
    # 1. Zielseite in privat/ anlegen
    r1 = client.post(
        f"{API}/pages",
        json={
            "path": "privat/02-finanzen-steuern/haftpflicht",
            "title": "Privathaftpflicht",
            "body": "# Privathaftpflicht\n\nTarif Komfort, Beitrag 64 EUR pro Jahr. #versicherung #finanzen\n",
        },
    )
    assert r1.status_code == 200, r1.text
    target_id = r1.json()["page"]["id"]

    # 2. Zweite Seite verlinkt per [[Wikilink]] + einem Geister-Link [[noch-offen]]
    r2 = client.post(
        f"{API}/pages",
        json={
            "path": "privat/01-alltag-wohnen/wohnung-checkliste",
            "title": "Wohnung Checkliste",
            "body": (
                "# Wohnung\n\n"
                "Wichtig: Police prüfen unter [[privat/02-finanzen-steuern/haftpflicht|Haftpflicht-Police]].\n"
                "Außerdem später anlegen: [[privat/01-alltag-wohnen/stromvertrag]].\n"
            ),
        },
    )
    assert r2.status_code == 200, r2.text
    src_id = r2.json()["page"]["id"]

    # 3. Backlinks auf der Haftpflicht-Seite prüfen
    d1 = client.get(f"{API}/pages/{target_id}").json()
    assert any(b["source_id"] == src_id for b in d1["backlinks"])
    assert "versicherung" in d1["tags"]

    # 4. Wissensgraph enthält Knoten, Kante und den Geister-Knoten
    g = client.get(f"{API}/graph", params={"space": "privat"}).json()
    node_ids = {n["id"] for n in g["nodes"]}
    assert target_id in node_ids
    assert src_id in node_ids
    assert any(n["kind"] == "ghost" and "stromvertrag" in n["path"] for n in g["nodes"])
    assert any(e["source"] == src_id and e["target"] == target_id for e in g["edges"])

    # 5. Zielseite verschieben -> eingehender [[Wikilink]] in Wohnung-Checkliste wird automatisch umgeschrieben!
    mv = client.post(
        f"{API}/pages/{target_id}/move",
        json={"to_path": "privat/99-archiv/haftpflicht-alt", "title": "Privathaftpflicht (Alt)"},
    )
    assert mv.status_code == 200, mv.text
    assert mv.json()["rewritten_backlinks"] == 1

    updated_src = client.get(f"{API}/pages/{src_id}").json()
    assert "[[privat/99-archiv/haftpflicht-alt|Haftpflicht-Police]]" in updated_src["body"]
    assert not (server_env["data_dir"] / "vault/privat/02-finanzen-steuern/haftpflicht.md").exists()
    assert (server_env["data_dir"] / "vault/privat/99-archiv/haftpflicht-alt.md").is_file()

    # 6. Geister-Seite nachträglich anlegen -> offener Link löst sich automatisch auf!
    r3 = client.post(
        f"{API}/pages",
        json={
            "path": "privat/01-alltag-wohnen/stromvertrag",
            "title": "Stromvertrag",
            "body": "Ökostrom 29 ct/kWh.",
        },
    )
    assert r3.status_code == 200
    strom_id = r3.json()["page"]["id"]
    src_after = client.get(f"{API}/pages/{src_id}").json()
    assert any(o["target_id"] == strom_id and o["exists"] for o in src_after["outgoing_links"])


def test_fts5_search_and_three_way_offline_merge(client):
    # Volltextsuche mit deutschem Präfix
    hits = client.get(f"{API}/search", params={"q": "Ökostrom"}).json()["results"]
    assert any(h["path"] == "privat/01-alltag-wohnen/stromvertrag" for h in hits)

    # 3-Wege-Merge: zwei unabhängige Änderungen auf derselben Basis-Revision verschmelzen konfliktfrei
    base = client.post(
        f"{API}/pages",
        json={
            "path": "privat/04-arbeit-projekte/merge-test",
            "title": "Merge Test",
            "body": "Zeile 1\nZeile 2\nZeile 3\nZeile 4\nZeile 5\n",
        },
    ).json()
    base_rev = base["revision_id"]
    pid = base["page"]["id"]

    # Server ändert Zeile 1
    s1 = client.put(
        f"{API}/pages/{pid}",
        json={"title": "Merge Test", "body": "Zeile 1 NEU\nZeile 2\nZeile 3\nZeile 4\nZeile 5\n"},
    )
    assert s1.status_code == 200

    # Offline-Client sendet mit `expected_revision=base_rev` eine Änderung in Zeile 5
    s2 = client.put(
        f"{API}/pages/{pid}",
        json={
            "title": "Merge Test",
            "body": "Zeile 1\nZeile 2\nZeile 3\nZeile 4\nZeile 5 OFFLINE\n",
            "expected_revision": base_rev,
        },
    )
    assert s2.status_code == 200, s2.text
    assert s2.json()["merged"] is True
    merged_body = s2.json()["page"]["body"]
    assert "Zeile 1 NEU" in merged_body
    assert "Zeile 5 OFFLINE" in merged_body

    # Überlappende Änderung derselben Zeile -> sauberer 409 Conflict mit Server-Stand
    s3 = client.put(
        f"{API}/pages/{pid}",
        json={
            "title": "Merge Test",
            "body": "Zeile 1 KONFLIKT\nZeile 2\nZeile 3\nZeile 4\nZeile 5\n",
            "expected_revision": base_rev,
        },
    )
    assert s3.status_code == 409
    assert "server" in s3.json()
