"""Quellen automatisch ausfüllen: Webadresse (Seitentitel), ISBN (Open Library) oder DOI (Crossref).
Der Server ruft nur öffentliche Adressen ab (kein Zugriff auf lokale oder interne Netze)."""
import asyncio
import html
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx

UA = "IDA-Docs/1.0 (Quellenangaben)"
MAX_BYTES = 600_000


class LookupFehler(ValueError):
    pass


def _is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


async def _get(client: httpx.AsyncClient, url: str, limit: int = MAX_BYTES) -> tuple[str, str]:
    """GET mit Weiterleitungen, jede Station wird geprüft. Gibt (Text, Endadresse) zurück."""
    for _ in range(5):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise LookupFehler("Ungültige Adresse")
        if not await asyncio.to_thread(_is_public, u.hostname):
            raise LookupFehler("Diese Adresse darf nicht abgerufen werden")
        async with client.stream("GET", url) as r:
            if r.is_redirect and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code >= 400:
                raise LookupFehler(f"Seite nicht erreichbar ({r.status_code})")
            buf = b""
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) >= limit:
                    break
            return buf.decode(r.encoding or "utf-8", errors="replace"), url
    raise LookupFehler("Zu viele Weiterleitungen")


def parse_page(page: str, url: str) -> dict:
    metas = {}
    for m in re.finditer(r"<meta\s+([^>]+?)/?>", page, re.I):
        a = {k.lower(): (dq or sq) for k, dq, sq in re.findall(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""", m.group(1))}
        key = (a.get("property") or a.get("name") or "").lower()
        if key and a.get("content"):
            metas.setdefault(key, html.unescape(a["content"]).strip())
    t = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    title = metas.get("og:title") or (html.unescape(re.sub(r"\s+", " ", t.group(1))).strip() if t else "")
    date = metas.get("article:published_time") or metas.get("datepublished") or metas.get("date") or metas.get("dc.date") or ""
    year = (re.search(r"\b(19|20)\d\d\b", date) or [""])[0] if date else ""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return {"type": "web", "title": title[:300], "author": metas.get("author") or metas.get("article:author") or "",
            "publisher": metas.get("og:site_name") or host, "year": year, "url": url, "accessed": time.strftime("%Y-%m-%d")}


def _name(full: str) -> str:
    """'Anna Maria Müller' → 'Müller, Anna Maria'"""
    parts = full.split()
    return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else full


async def _isbn(client: httpx.AsyncClient, isbn: str) -> dict:
    text, _ = await _get(client, f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data")
    import json
    d = json.loads(text).get(f"ISBN:{isbn}")
    if not d:
        raise LookupFehler("Zu dieser ISBN habe ich nichts gefunden – bitte von Hand eintragen")
    title = d.get("title", "") + (f": {d['subtitle']}" if d.get("subtitle") else "")
    return {"type": "buch", "title": title, "author": "; ".join(_name(a["name"]) for a in d.get("authors", [])),
            "publisher": (d.get("publishers") or [{}])[0].get("name", ""), "place": (d.get("publish_places") or [{}])[0].get("name", ""),
            "year": (re.search(r"\b(1[5-9]|20)\d\d\b", d.get("publish_date", "")) or [""])[0]}


async def _doi(client: httpx.AsyncClient, doi: str) -> dict:
    import json
    try:
        text, _ = await _get(client, f"https://api.crossref.org/works/{doi}")
    except LookupFehler as e:
        raise LookupFehler("Zu dieser DOI habe ich nichts gefunden") from e
    m = json.loads(text)["message"]
    year = ((m.get("issued") or {}).get("date-parts") or [[""]])[0][0]
    pages = m.get("page", "")
    note = ", ".join(x for x in (f"{m['volume']}" + (f"({m['issue']})" if m.get("issue") else "") if m.get("volume") else "", f"S. {pages}" if pages else "") if x)
    return {"type": "artikel" if m.get("type") == "journal-article" else "buch" if m.get("type") == "book" else "sonst",
            "title": (m.get("title") or [""])[0], "container": (m.get("container-title") or [""])[0], "publisher": m.get("publisher", ""),
            "author": "; ".join(f"{a.get('family', '')}, {a.get('given', '')}".strip(", ") for a in m.get("author", [])),
            "year": str(year or ""), "note": note, "url": f"https://doi.org/{doi}"}


async def lookup(q: str) -> dict:
    q = (q or "").strip()
    if not q:
        raise LookupFehler("Bitte Adresse, ISBN oder DOI angeben")
    doi = re.search(r"\b10\.\d{4,9}/\S+", q)
    isbn = re.sub(r"[\s-]", "", q)
    try:
        async with httpx.AsyncClient(timeout=8, headers={"user-agent": UA}) as c:
            if doi and not re.match(r"https?://(?!(dx\.)?doi\.org)", q, re.I):
                return await _doi(c, doi.group(0).rstrip(".,;)"))
            if re.match(r"https?://", q, re.I):
                page, final = await _get(c, q)
                return parse_page(page, final)
            if re.fullmatch(r"\d{9}[\dXx]|\d{13}", isbn):
                return await _isbn(c, isbn.upper())
    except httpx.HTTPError as e:
        raise LookupFehler("Konnte nichts abrufen – bitte von Hand eintragen") from e
    raise LookupFehler("Das sieht weder nach Webadresse noch nach ISBN oder DOI aus")
