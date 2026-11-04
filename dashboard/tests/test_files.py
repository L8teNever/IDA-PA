"""Tool „Dateien“: ansehen, ändern, hochladen, entpacken – und dass nichts aus dem Bereich ausbricht."""
import asyncio
import io
import json
import os
import tarfile
import zipfile

import pytest

from app.config import get_settings
from app.tools import files as fs

API = "/api/v1/fs"
UP = {"x-ida-upload": "1", "content-type": "application/octet-stream"}


@pytest.fixture
def root(tmp_path, settings, monkeypatch):
    r = tmp_path / "server"
    (r / "etc").mkdir(parents=True)
    (r / "home" / "simon").mkdir(parents=True)
    (r / "proc").mkdir()
    (r / "etc" / "passwd").write_text(f"simon:x:{os.getuid()}:{os.getgid()}::/home/simon:/bin/sh\n")
    (r / "home" / "simon" / "a.txt").write_text("hallo\n")
    monkeypatch.setenv("FILES_ROOT", str(r))
    get_settings.cache_clear()
    fs._users = (0.0, {})
    yield r
    get_settings.cache_clear()


async def done(client, r):
    """Folgt einem Hintergrund-Vorgang bis zum Ende."""
    job = r.json()
    assert r.status_code == 200, job
    async with client.stream("GET", f"/api/v1/jobs/{job['id']}/live") as s:
        lines = [json.loads(x) async for x in s.aiter_lines() if x]
    return next(x["done"] for x in lines if "done" in x)


async def put(client, root_dir, name, data, **q):
    return await client.put(f"{API}/upload", params={"dir": root_dir, "name": name, "upload_id": "abcd1234", "total": len(data), **q}, content=data, headers=UP)


async def test_list_and_read(client, root):
    r = (await client.get(f"{API}/list")).json()
    assert r["path"] == "/" and r["parent"] is None and [e["name"] for e in r["entries"]] == ["etc", "home", "proc"]
    r = (await client.get(f"{API}/list", params={"path": "/home/simon"})).json()
    e = r["entries"][0]
    assert r["parent"] == "/home" and e["path"] == "/home/simon/a.txt" and e["size"] == 6 and e["owner"] == "simon"
    assert (await client.get(f"{API}/text", params={"path": "/home/simon/a.txt"})).json()["content"] == "hallo\n"
    (root / "bin.dat").write_bytes(b"\0\1\2")
    assert (await client.get(f"{API}/text", params={"path": "/bin.dat"})).status_code == 415
    assert (await client.get(f"{API}/text", params={"path": "/fehlt"})).status_code == 404


async def test_nothing_gets_out(client, root, tmp_path):
    secret = tmp_path / "geheim.txt"
    secret.write_text("x")
    # absoluter Link meint den Server, nicht den Container
    os.symlink(str(secret), root / "link")
    os.symlink("/home/simon/a.txt", root / "ok")
    os.symlink("../../../..", root / "home" / "hoch")
    assert (await client.get(f"{API}/text", params={"path": "/link"})).status_code == 404
    assert (await client.get(f"{API}/text", params={"path": "/ok"})).json()["content"] == "hallo\n"
    assert (await client.get(f"{API}/list", params={"path": "/home/hoch"})).json()["path"] == "/"
    assert (await client.get(f"{API}/text", params={"path": "/../../geheim.txt"})).status_code == 404
    assert (await client.get(f"{API}/text", params={"path": "/home/hoch/../geheim.txt"})).status_code == 404
    for bad in ("../x", "a/b", "..", ""):
        assert (await client.post(f"{API}/new", json={"dir": "/", "name": bad})).status_code == 400
    assert (await put(client, "/home", "../../raus.txt", b"x")).status_code == 400
    assert not (root.parent / "raus.txt").exists()


async def test_protected(client, root):
    for p in ("/", "/etc", "/proc/x"):
        assert (await client.post(f"{API}/delete", json={"items": [p]})).status_code == 403, p
    assert (await client.post(f"{API}/rename", json={"path": "/etc", "name": "weg"})).status_code == 403
    assert (await client.post(f"{API}/new", json={"dir": "/proc", "name": "x"})).status_code == 403
    assert (root / "etc").is_dir()


async def test_edit_keeps_mode_and_finds_conflicts(client, root):
    f = root / "home" / "simon" / "a.txt"
    f.chmod(0o640)
    d = (await client.get(f"{API}/text", params={"path": "/home/simon/a.txt"})).json()
    r = await client.put(f"{API}/text", json={"path": "/home/simon/a.txt", "content": "neu ü\n", "version": d["version"]})
    assert r.status_code == 200 and f.read_text() == "neu ü\n" and oct(f.stat().st_mode)[-3:] == "640"
    r2 = await client.put(f"{API}/text", json={"path": "/home/simon/a.txt", "content": "alt", "version": d["version"]})
    assert r2.status_code == 409 and f.read_text() == "neu ü\n"
    assert not [x for x in os.listdir(f.parent) if x.endswith(fs.PART)]


async def test_new_rename(client, root):
    assert (await client.post(f"{API}/new", json={"dir": "/home", "name": "ordner", "folder": True})).json()["type"] == "dir"
    assert (await client.post(f"{API}/new", json={"dir": "/home/ordner", "name": "b.md"})).json()["size"] == 0
    assert (await client.post(f"{API}/new", json={"dir": "/home/ordner", "name": "b.md"})).status_code == 409
    assert (await client.post(f"{API}/rename", json={"path": "/home/ordner/b.md", "name": "c.md"})).json()["name"] == "c.md"
    assert (root / "home" / "ordner" / "c.md").exists()


async def test_upload_in_pieces_and_folders(client, root):
    data = b"a" * 10 + b"b" * 5
    q = {"dir": "/home", "name": "sub/dir/g.bin", "upload_id": "abcd1234", "total": 15}
    r = await client.put(f"{API}/upload", params=q, content=data[:10], headers=UP)
    assert r.json() == {"size": 10, "done": False} and not (root / "home" / "sub" / "dir" / "g.bin").exists()
    assert not [e for e in (await client.get(f"{API}/list", params={"path": "/home/sub/dir"})).json()["entries"]]  # läuft noch: unsichtbar
    assert (await client.put(f"{API}/upload", params={**q, "offset": 3}, content=data[10:], headers=UP)).status_code == 409  # Stück fehlt
    r = await client.put(f"{API}/upload", params={**q, "offset": 10}, content=data[10:], headers=UP)
    assert r.json()["done"] and (root / "home" / "sub" / "dir" / "g.bin").read_bytes() == data
    assert (await put(client, "/home/sub/dir", "g.bin", b"x")).status_code == 409
    assert (await put(client, "/home/sub/dir", "g.bin", b"x", overwrite=True)).json()["done"]
    assert (await put(client, "/home", "leer.txt", b"")).json()["done"] and (root / "home" / "leer.txt").read_bytes() == b""
    # ein Unterordner, der ein Link ist, zeigt auf den Server, nicht in den Container
    os.symlink("/etc", root / "home" / "lnk")
    assert (await put(client, "/home", "lnk/neu.txt", b"x")).json()["done"] and (root / "etc" / "neu.txt").read_bytes() == b"x"
    # ohne den Header (fremde Seite): nein
    assert (await client.put(f"{API}/upload", params=q, content=b"x", headers={"content-type": "application/octet-stream"})).status_code == 415


async def test_copy_move_delete(client, root):
    h = root / "home" / "simon"
    (root / "home" / "ziel").mkdir()
    (h / "ordner").mkdir()
    (h / "ordner" / "x.txt").write_text("x")
    # gleicher Ordner: Kopie mit neuem Namen
    assert (await done(client, await client.post(f"{API}/paste", json={"items": ["/home/simon/a.txt"], "dest": "/home/simon"})))["ok"]
    assert (h / "a Kopie.txt").read_text() == "hallo\n"
    assert (await done(client, await client.post(f"{API}/paste", json={"items": ["/home/simon/a.txt"], "dest": "/home/simon"})))["ok"]
    assert (h / "a Kopie 2.txt").exists()
    assert (await done(client, await client.post(f"{API}/paste", json={"items": ["/home/simon/ordner"], "dest": "/home/ziel"})))["ok"]
    assert (root / "home" / "ziel" / "ordner" / "x.txt").exists() and (h / "ordner").exists()
    # Konflikt: vorher sichtbar, alle drei Wege
    body = {"items": ["/home/simon/ordner"], "dest": "/home/ziel", "move": True}
    assert (await client.post(f"{API}/conflicts", json=body)).json()["names"] == ["ordner"]
    (h / "ordner" / "neu.txt").write_text("n")
    assert (await done(client, await client.post(f"{API}/paste", json={**body, "conflict": "skip"})))["ok"]
    assert (h / "ordner").exists()
    assert (await done(client, await client.post(f"{API}/paste", json={**body, "conflict": "replace"})))["ok"]
    assert not (h / "ordner").exists() and (root / "home" / "ziel" / "ordner" / "neu.txt").exists()
    # in sich selbst geht nicht
    assert (await client.post(f"{API}/paste", json={"items": ["/home/ziel"], "dest": "/home/ziel/ordner"})).status_code == 400
    assert (await done(client, await client.post(f"{API}/delete", json={"items": ["/home/ziel/ordner", "/home/simon/a Kopie.txt"]})))["ok"]
    assert not (root / "home" / "ziel" / "ordner").exists() and not (h / "a Kopie.txt").exists()


async def test_delete_hits_the_link_not_its_target(client, root):
    os.symlink("/home/simon", root / "home" / "kurz")
    assert (await done(client, await client.post(f"{API}/delete", json={"items": ["/home/kurz"]})))["ok"]
    assert not (root / "home" / "kurz").is_symlink() and (root / "home" / "simon" / "a.txt").exists()


async def test_extract_and_compress(client, root):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("x/y.txt", "drin")
        f.writestr("../../boese.txt", "raus")
    assert (await put(client, "/home", "paket.zip", z.getvalue())).json()["done"]
    r = await client.post(f"{API}/extract", json={"path": "/home/paket.zip"})
    assert r.json()["dest"] == "/home/paket" and (await done(client, r))["ok"]
    assert (root / "home" / "paket" / "x" / "y.txt").read_text() == "drin"
    assert not (root / "boese.txt").exists() and not (root / "home" / "boese.txt").exists()
    # gibt es den Ordner schon, kommt ein neuer
    r = await client.post(f"{API}/extract", json={"path": "/home/paket.zip"})
    assert r.json()["dest"] == "/home/paket 2" and (await done(client, r))["ok"]
    # tar mit Ausbruch: wird abgelehnt, nichts bleibt liegen
    t = io.BytesIO()
    with tarfile.open(fileobj=t, mode="w:gz") as f:
        i = tarfile.TarInfo("../raus.txt")
        i.size = 1
        f.addfile(i, io.BytesIO(b"x"))
    assert (await put(client, "/home", "b.tar.gz", t.getvalue())).json()["done"]
    assert not (await done(client, await client.post(f"{API}/extract", json={"path": "/home/b.tar.gz"})))["ok"]
    assert not (root / "home" / "raus.txt").exists() and not (root / "home" / "b").exists()
    assert (await client.post(f"{API}/extract", json={"path": "/home/simon/a.txt"})).status_code == 400
    r = await client.post(f"{API}/compress", json={"items": ["/home/paket"]})
    assert r.json()["dest"] == "/home/paket.zip" or r.json()["dest"] == "/home/paket 2.zip"
    assert (await done(client, r))["ok"]
    with zipfile.ZipFile(root / r.json()["dest"].lstrip("/")) as f:
        assert "paket/x/y.txt" in f.namelist()


async def test_raw_is_safe(client, root):
    (root / "s.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>")
    (root / "p.html").write_text("<script>1</script>")
    (root / "b.png").write_bytes(b"\x89PNG")
    (root / "x.bin").write_bytes(b"\0")
    for n in ("s.svg", "p.html"):
        r = await client.get(f"{API}/raw", params={"path": "/" + n})
        assert "sandbox" in r.headers["content-security-policy"]
    r = await client.get(f"{API}/raw", params={"path": "/b.png"})
    assert r.headers["content-type"] == "image/png" and r.headers["content-disposition"].startswith("inline")
    r = await client.get(f"{API}/raw", params={"path": "/x.bin"})
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["content-disposition"].startswith("attachment")
    r = await client.get(f"{API}/raw", params={"path": "/p.html", "download": True})
    assert r.headers["content-disposition"].startswith("attachment") and r.headers["content-type"] == "application/octet-stream"
    assert (await client.get(f"{API}/raw", params={"path": "/proc/x"})).status_code == 403


async def test_without_the_mount_only_the_fallback(client, tmp_path, settings, monkeypatch):
    opt = tmp_path / "opt"
    (opt / "stacks").mkdir(parents=True)
    monkeypatch.setenv("FILES_ROOT", str(tmp_path / "fehlt"))
    monkeypatch.setenv("FILES_FALLBACK", str(opt))
    get_settings.cache_clear()
    r = (await client.get(f"{API}/list")).json()
    assert r["path"] == str(opt) and r["root"] == str(opt) and r["parent"] is None and r["entries"][0]["name"] == "stacks"
    assert (await client.get(f"{API}/list", params={"path": "/etc"})).status_code == 403
    assert (await client.post(f"{API}/delete", json={"items": [str(opt)]})).status_code == 403
    get_settings.cache_clear()
    await asyncio.sleep(0)
