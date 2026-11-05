"""Ist eine öffentliche Adresse offen oder mit Cloudflare Zero Trust (Access) geschützt?
Ganz ohne API-Token: das Dashboard ruft die Adresse einmal ohne Anmeldung auf (wie ein fremder Besucher). Steht Access davor,
leitet Cloudflare auf die Login-Seite (…cloudflareaccess.com, /cdn-cgi/access/…) um oder lehnt mit seiner Access-Seite ab.
Kommt dagegen die App selbst zurück, ist die Adresse für jeden erreichbar. Das Ergebnis gilt eine Weile (TTL)."""
import asyncio
import logging
import time

import httpx

from . import cloudflare_api

log = logging.getLogger("dashboard.access")

TTL = 300           # so lange (Sekunden) gilt ein Ergebnis
TIMEOUT = 4
ACCESS = "access"   # Login über Cloudflare Access nötig
PUBLIC = "public"   # für jeden erreichbar
UNKNOWN = "unknown"  # nicht feststellbar (nicht erreichbar, Bot-Schutz …)

_cache: dict[str, tuple[float, dict]] = {}


def _is_access_url(u: str) -> bool:
    u = (u or "").lower()
    return "cloudflareaccess.com" in u or "/cdn-cgi/access/" in u


def classify(status: int, headers: dict, body: str = "") -> dict:
    """{state, detail} aus der Antwort auf einen Aufruf ohne Anmeldung."""
    h = {k.lower(): v for k, v in headers.items()}
    if 300 <= status < 400 and _is_access_url(h.get("location", "")):
        return {"state": ACCESS, "detail": "Cloudflare Access leitet zum Login um"}
    if status in (401, 403):
        if any(k.startswith("cf-access") for k in h) or _is_access_url(body) or "cloudflare access" in body.lower():
            return {"state": ACCESS, "detail": "Cloudflare Access lässt nur Angemeldete durch"}
    if h.get("cf-mitigated") == "challenge":
        return {"state": UNKNOWN, "detail": "Cloudflare-Bot-Schutz antwortet – Access nicht feststellbar"}
    if status >= 500:
        return {"state": UNKNOWN, "detail": f"Antwort {status} – die Seite selbst hat gerade ein Problem"}
    return {"state": PUBLIC, "detail": f"Ohne Anmeldung erreichbar (Antwort {status})"}


async def fetch(url: str) -> tuple[int, dict, str]:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, headers={"user-agent": "IDA-Dashboard/access-check"}) as c:
        r = await c.get(url)
        body = r.text[:20000] if r.status_code in (401, 403) else ""
        return r.status_code, dict(r.headers), body


async def check(url: str) -> dict:
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    try:
        res = classify(*await fetch(url))
    except (httpx.HTTPError, ValueError) as e:
        log.info("Access-Prüfung für %s fehlgeschlagen: %s", url, e)
        res = {"state": UNKNOWN, "detail": "Nicht erreichbar – Schutz nicht feststellbar"}
    _cache[url] = (time.monotonic(), res)
    return res


async def annotate(urls: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Hängt an jede Adresse {access, access_detail, access_source} an. Zuerst aus der Cloudflare-API (Access-Apps, sicher),
    sonst per Aufruf ohne Anmeldung (alle parallel)."""
    flat = [u for lst in urls.values() for u in lst]
    known = await cloudflare_api.access_for([u["url"] for u in flat]) or {}
    rest = [u for u in flat if u["url"] not in known]
    probed = dict(zip((u["url"] for u in rest), await asyncio.gather(*(check(u["url"]) for u in rest))))
    for u in flat:
        res = known.get(u["url"]) or {**probed[u["url"]], "source": "probe"}
        u["access"], u["access_detail"], u["access_source"] = res["state"], res["detail"], res["source"]
    return urls


def reset() -> None:
    _cache.clear()
