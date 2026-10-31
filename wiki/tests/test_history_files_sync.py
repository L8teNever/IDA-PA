"""Tests für Dateianhänge, KI-Chat- & Code-Projekt-Spiegel (`ki/`), Versionsverlauf, Papierkorb, Delta-Sync & Backup."""
import base64
import io
import zipfile
from datetime import datetime, timedelta, timezone

from conftest import API

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_attachments_upload_download_and_history_restore(client):
    p = client.post(
        f"{API}/pages",
        json={"path": "privat/05-wissen-lernen/buchnotiz", "title": "Buchnotiz v1", "body": "Erste Fassung.\n"},
    ).json()
    pid = p["page"]["id"]
    rev1 = p["revision_id"]

    # Zweite Version speichern
    p2 = client.put(f"{API}/pages/{pid}", json={"title": "Buchnotiz v2", "body": "Zweite deutlich längere Fassung.\n"}).json()
    rev2 = p2["revision_id"]
    assert rev2 > rev1

    # PNG-Bild und PDF-Datei anhängen
    up_img = client.post(f"{API}/files/{pid}", files={"file": ("skizze.png", TINY_PNG, "image/png")})
    assert up_img.status_code == 200, up_img.text
    assert up_img.json()["attachment"]["kind"] == "image"

    up_pdf = client.post(f"{API}/files/{pid}", files={"file": ("beleg.pdf", b"%PDF-1.4 Testinhalt", "application/pdf")})
    assert up_pdf.status_code == 200
    assert up_pdf.json()["attachment"]["kind"] == "pdf"

    # Abruf inkl. ETag / 304
    dl = client.get(f"{API}/files/{pid}/skizze.png")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "image/png"
    etag = dl.headers["etag"]
    assert client.get(f"{API}/files/{pid}/skizze.png", headers={"If-None-Match": etag}).status_code == 304

    # Diff einer Revision abrufen und auf Version 1 zurücksetzen
    diff_info = client.get(f"{API}/revisions/{rev2}").json()
    assert "+Zweite deutlich längere Fassung." in diff_info["diff"]

    restored = client.post(f"{API}/pages/{pid}/restore/{rev1}")
    assert restored.status_code == 200
    assert "Erste Fassung." in restored.json()["page"]["body"]


def test_ki_mirror_chats_code_projects_and_code_threads(client, agent_client):
    sync_c = agent_client("system:sync")

    # 1. Agent spiegeln
    r_ag = sync_c.post(
        f"{API}/internal/sync/agent",
        json={"id": "main", "name": "Ida", "role": "Persönliche Hauptassistentin", "model": "gpt-5", "tools": ["wiki_search", "kalender"]},
    )
    assert r_ag.status_code == 200, r_ag.text

    # 2. Code-Projekt spiegeln
    r_pr = sync_c.post(
        f"{API}/internal/sync/project",
        json={
            "id": "proj12345678",
            "name": "IDA Monorepo",
            "agent_id": "main",
            "git_branch": "main",
            "description": "Weiterentwicklung von IDA, Kalender & Wiki",
            "ida_url": "https://ida.kulbarts.com/#/code/proj12345678",
        },
    )
    assert r_pr.status_code == 200, r_pr.text

    # 3. Normalen Chat inkl. Bildanhang spiegeln
    r_ch = sync_c.post(
        f"{API}/internal/sync/chat",
        json={
            "id": "chat11112222",
            "kind": "chat",
            "agent_id": "main",
            "agent_name": "Ida",
            "title": "Urlaubsplanung Portugal",
            "created_at": "2026-10-30T10:00:00+00:00",
            "updated_at": "2026-10-30T10:05:00+00:00",
            "ida_url": "https://ida.kulbarts.com/#/chat/chat11112222",
            "messages": [
                {"id": "m1", "role": "user", "content": "Hier ist der Screenshot der Verbindung.", "created_at": "2026-10-30T10:00:00Z"},
                {"id": "m2", "role": "assistant", "content": "Sieht super aus – Direktflug ab Düsseldorf!", "created_at": "2026-10-30T10:01:00Z"},
            ],
            "files": [
                {
                    "id": "cf0011223344",
                    "message_id": "m1",
                    "name": "flug.png",
                    "mime": "image/png",
                    "data_b64": base64.b64encode(TINY_PNG).decode("ascii"),
                }
            ],
        },
    )
    assert r_ch.status_code == 200, r_ch.text
    chat_page = r_ch.json()["page"]
    assert chat_page["path"].startswith("ki/main/chats/2026-10-30__urlaubsplanung-portugal__chat11112222")
    assert any(a["name"] == "flug.png" for a in chat_page["attachments"])

    # 4. Code-Thread unter dem Code-Projekt spiegeln (inkl. Tool-Call-Diff!)
    r_code = sync_c.post(
        f"{API}/internal/sync/chat",
        json={
            "id": "code99998888",
            "kind": "code",
            "agent_id": "main",
            "agent_name": "Ida",
            "project_id": "proj12345678",
            "project_name": "IDA Monorepo",
            "title": "Wiki Docker-Integration",
            "created_at": "2026-10-30T11:00:00+00:00",
            "updated_at": "2026-10-30T11:15:00+00:00",
            "ida_url": "https://ida.kulbarts.com/#/code/proj12345678/code99998888",
            "messages": [
                {"id": "cm1", "role": "user", "content": "Füge den Wiki-Dienst in docker-compose hinzu."},
                {
                    "id": "cm2",
                    "role": "assistant",
                    "content": "Ich habe den Dienst ergänzt.",
                    "tool_calls": [
                        {
                            "name": "edit_file",
                            "args": {"file": "docker-compose.yml"},
                            "result": "--- a/docker-compose.yml\n+++ b/docker-compose.yml\n+  wiki:\n+    build: ./wiki\n",
                        }
                    ],
                },
            ],
        },
    )
    assert r_code.status_code == 200, r_code.text
    code_page = r_code.json()["page"]
    assert "ki/main/code/ida-monorepo__proj12345678/" in code_page["path"]
    assert "```diff" in code_page["body"]

    # Sync-State meldet Agent, Code-Projekt und beide Threads zurück
    st = sync_c.get(f"{API}/internal/sync/state").json()
    assert "main" in st["agents"]
    assert "proj12345678" in st["projects"]
    assert "chat11112222" in st["chats"]
    assert "code99998888" in st["chats"]


def test_delta_sync_trash_purge_ask_ida_and_backup_zip(client, fake_ida):
    # Initialer Sync + Delta-Sync
    full = client.get(f"{API}/sync", params={"since": 0}).json()
    assert full["full"] is True
    cursor = full["cursor"]
    assert cursor > 0

    temp_page = client.post(
        f"{API}/pages",
        json={"path": "privat/00-inbox/kurz-notiz", "title": "Kurznotiz", "body": "Wird gleich gelöscht."},
    ).json()["page"]

    delta = client.get(f"{API}/sync", params={"since": cursor}).json()
    assert delta["full"] is False
    assert any(p["id"] == temp_page["id"] for p in delta["pages"])

    # In Papierkorb verschieben -> nach 31 Tagen durch Maintenance endgültig bereinigt
    client.delete(f"{API}/pages/{temp_page['id']}")
    trash = client.get(f"{API}/trash").json()["items"]
    assert any(t["id"] == temp_page["id"] for t in trash)

    future = (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()
    maint = client.post(f"{API}/_test/maintenance", params={"now": future}).json()
    assert maint["purged_trash"] >= 1
    assert all(t["id"] != temp_page["id"] for t in client.get(f"{API}/trash").json()["items"])

    # „Ida fragen“ leitet Frage + Seitenkontext an IDA weiter
    ask = client.post(
        f"{API}/ask",
        json={"question": "Fasse diese Seite zusammen", "page_path": "privat/_start", "page_title": "Start"},
    )
    assert ask.status_code == 200
    assert ask.json()["answer"] == fake_ida.answer_text
    assert fake_ida.received[-1]["page_path"] == "privat/_start"

    # Backup & ZIP-Export enthalten `vault/` und `wiki.db`
    bkp = client.post(f"{API}/backups")
    assert bkp.status_code == 200
    assert bkp.json()["backup"]["name"].startswith("wiki-")

    exp = client.get(f"{API}/export.zip")
    assert exp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(exp.content))
    assert "vault/privat/_start.md" in zf.namelist()
