"""Sandbox eines Code-Threads: spricht mit dem Sandbox-Dienst (app/sandbox), der die Container verwaltet.
Das Projekt kommt als Kopie hinein, ohne .git und .env-Dateien; was dort passiert, ändert das echte Projekt nicht."""
import asyncio
import io
import subprocess
import tarfile
from pathlib import Path

import httpx

from ...config import get_settings
from ..errors import Invalid

MAX_COPY = 200 * 1024 * 1024


def enabled() -> bool:
    s = get_settings()
    return bool(s.sandbox_url and s.sandbox_secret)


async def _call(method: str, path: str, *, timeout: float = 30, **kw) -> dict:
    s = get_settings()
    if not enabled():
        raise Invalid("Die Sandbox ist nicht eingerichtet (SANDBOX_URL, SANDBOX_SECRET)")
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, s.sandbox_url.rstrip("/") + path, headers={"x-sandbox-secret": s.sandbox_secret, **kw.pop("headers", {})}, **kw)
    except httpx.TimeoutException as e:
        raise Invalid("Die Sandbox antwortet nicht rechtzeitig") from e
    except httpx.HTTPError as e:
        raise Invalid("Die Sandbox ist gerade nicht erreichbar") from e
    if r.status_code >= 400:
        raise Invalid(r.json().get("detail", "Fehler in der Sandbox") if "json" in r.headers.get("content-type", "") else "Fehler in der Sandbox")
    return r.json()


def project_tar(root: Path) -> bytes:
    """Alle Dateien, die git kennt oder die nicht ignoriert sind, ohne Geheimnisse."""
    names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root, capture_output=True, check=True).stdout.split(b"\0")
    buf, total = io.BytesIO(), 0
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for raw in names:
            rel = raw.decode("utf-8", "replace")
            p = root / rel
            parts = Path(rel).parts
            if not rel or ".git" in parts or ".ida" in parts or any(x == ".env" or x.startswith(".env.") and x != ".env.example" for x in parts):
                continue
            if p.is_symlink() or not p.is_file():
                continue
            total += p.stat().st_size
            if total > MAX_COPY:
                raise Invalid("Das Projekt ist zu groß für die Sandbox (höchstens 200 MB)")
            tar.add(p, arcname=rel, recursive=False)
    return buf.getvalue()


async def copy_project(thread: str, root: Path) -> None:
    data = await asyncio.to_thread(project_tar, root)
    await _call("PUT", f"/sandbox/{thread}/files", content=data, timeout=120)


async def run(thread: str, command: str, timeout_s: int = 120) -> dict:
    return await _call("POST", f"/sandbox/{thread}/run", json={"befehl": command, "timeout_s": timeout_s}, timeout=max(30, min(timeout_s, 600)) + 60)


async def status(thread: str) -> dict:
    return await _call("GET", f"/sandbox/{thread}")


async def stop(thread: str) -> dict:
    return await _call("POST", f"/sandbox/{thread}/stop", timeout=60)


async def memory(thread: str, gb: int) -> dict:
    return await _call("POST", f"/sandbox/{thread}/memory", json={"gb": gb}, timeout=60)
