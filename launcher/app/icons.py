"""Seiten-Symbole: holt das Icon einer App (Access-Logo, sonst von der Seite selbst) und merkt es sich auf der Platte.

Seiten hinter Cloudflare Access sind von außen nicht lesbar. Deshalb wird zuerst die interne Adresse des Tunnels
(z. B. http://10.7.0.1:8450) abgefragt, dann die öffentliche. Findet sich nichts, zeigt die Oberfläche den Anfangsbuchstaben.
"""
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .config import get_settings

MAX_BYTES = 600_000
TTL_OK = 7 * 86400
TTL_NONE = 86400
_LINK = re.compile(r"<link\b[^>]*>", re.I)


def _dir() -> Path:
    d = get_settings().data_dir / "icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _name(host: str) -> str:
    return re.sub(r"[^a-z0-9.-]", "_", host.lower())


def _candidates(html: str, base: str) -> list[str]:
    scored = []
    for tag in _LINK.findall(html):
        rel = re.search(r'rel=["\']([^"\']+)["\']', tag, re.I)
        href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
        if not rel or not href or "icon" not in rel.group(1).lower() or "mask-icon" in rel.group(1).lower():
            continue
        size = re.search(r'sizes=["\'](\d+)x\d+', tag, re.I)
        score = int(size.group(1)) if size else 32
        if "apple-touch" in rel.group(1).lower():
            score += 1000
        scored.append((score, urljoin(base, href.group(1))))
    return [u for _, u in sorted(scored, reverse=True)]


async def _image(c: httpx.AsyncClient, url: str) -> tuple[bytes, str] | None:
    try:
        r = await c.get(url)
    except httpx.HTTPError:
        return None
    ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if r.status_code != 200 or not ct.startswith("image/") or ct == "image/svg+xml" and b"<script" in r.content[:MAX_BYTES].lower():
        return None
    if not r.content or len(r.content) > MAX_BYTES:
        return None
    return r.content, ct


async def _find(app: dict) -> tuple[bytes, str] | None:
    bases = [b for b in (app.get("service") or "", f"https://{app['host']}") if b.startswith("http")]
    async with httpx.AsyncClient(timeout=6, follow_redirects=False) as c:
        if app.get("logo", "").startswith("http"):
            got = await _image(c, app["logo"])
            if got:
                return got
        for base in bases:
            base = base.rstrip("/") + "/"
            urls = []
            try:
                r = await c.get(base)
                if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                    urls = _candidates(r.text[:200_000], base)
            except httpx.HTTPError:
                pass
            for u in [*urls[:3], urljoin(base, "/favicon.ico")]:
                got = await _image(c, u)
                if got:
                    return got
    return None


async def icon(app: dict) -> tuple[bytes, str] | None:
    key = _name(app["host"])
    d = _dir()
    img, meta = d / f"{key}.img", d / f"{key}.type"
    now = time.time()
    if img.exists() and now - img.stat().st_mtime < TTL_OK:
        return img.read_bytes(), meta.read_text() if meta.exists() else "image/png"
    none = d / f"{key}.none"
    if none.exists() and now - none.stat().st_mtime < TTL_NONE:
        return None
    got = await _find(app)
    if not got:
        none.touch()
        return None
    img.write_bytes(got[0])
    meta.write_text(got[1])
    none.unlink(missing_ok=True)
    return got
