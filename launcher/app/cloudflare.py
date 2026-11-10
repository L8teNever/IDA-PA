"""Liest die Apps aus Cloudflare (nur lesend): Access-Apps (Zero Trust) und die Adressen der Tunnel.

Daraus entsteht die Liste für den Launcher. Neue Apps oder Adressen erscheinen von selbst, sobald die Liste neu geholt wird.
"""
import time
from fnmatch import fnmatch

import httpx

from .config import get_settings

_cache: dict[str, tuple[float, object]] = {}


class CloudflareError(Exception):
    pass


async def _get(path: str, params: dict | None = None):
    s = get_settings()
    try:
        async with httpx.AsyncClient(base_url=s.cloudflare_api_url.rstrip("/"), timeout=20,
                                     headers={"authorization": f"Bearer {s.cloudflare_api_token.strip()}"}) as c:
            r = await c.get(path, params=params)
    except httpx.HTTPError as e:
        raise CloudflareError(f"Cloudflare nicht erreichbar: {e}") from e
    try:
        data = r.json()
    except ValueError as e:
        raise CloudflareError(f"Cloudflare antwortet {r.status_code} ohne JSON") from e
    if not data.get("success", False):
        errs = "; ".join(f"[{e.get('code', '?')}] {e.get('message', '?')}" for e in data.get("errors") or []) or f"HTTP {r.status_code}"
        raise CloudflareError(errs)
    return data.get("result"), data.get("result_info") or {}


async def _all(path: str, params: dict | None = None) -> list:
    out, page = [], 1
    while True:
        res, info = await _get(path, {**(params or {}), "page": page, "per_page": 100})
        out += res or []
        if not info.get("total_pages") or page >= info["total_pages"] or not res:
            return out
        page += 1


async def account_id() -> str:
    own = get_settings().cloudflare_account_id.strip()
    if own:
        return own
    accs, _ = await _get("/accounts")
    if not accs:
        raise CloudflareError("Der Token sieht kein Konto (braucht Account-Rechte)")
    return accs[0]["id"]


def _hosts(a: dict) -> list[str]:
    doms = list(a.get("self_hosted_domains") or [])
    doms += [d.get("uri") for d in a.get("destinations") or [] if d.get("type", "public") == "public" and d.get("uri")]
    if a.get("domain"):
        doms.append(a["domain"])
    out = []
    for d in doms:
        h = (d or "").strip().lower().split("/")[0]
        if h and "*" not in h and "." in h and not h.endswith(".cloudflareaccess.com") and h not in out:
            out.append(h)
    return out


async def fetch() -> dict:
    """{apps: [{name, host, logo, protected, service}]} – roh aus Cloudflare, ohne Filter."""
    acc = await account_id()
    items: dict[str, dict] = {}
    for a in await _all(f"/accounts/{acc}/access/apps"):
        if a.get("app_launcher_visible") is False or a.get("type") in ("warp", "infrastructure", "rdp"):
            continue
        for h in _hosts(a):
            items.setdefault(h, {"name": a.get("name") or h, "host": h, "logo": a.get("logo_url") or "", "protected": True, "service": ""})
    for t in await _all(f"/accounts/{acc}/cfd_tunnel", {"is_deleted": "false"}):
        if t.get("remote_config") is False:
            continue
        try:
            cfg, _ = await _get(f"/accounts/{acc}/cfd_tunnel/{t['id']}/configurations")
        except CloudflareError:
            continue
        for r in ((cfg or {}).get("config") or {}).get("ingress") or []:
            h = (r.get("hostname") or "").strip().lower()
            if not h or "*" in h or "." not in h:
                continue
            e = items.get(h)
            if e:
                e["service"] = e["service"] or r.get("service") or ""
            else:
                items[h] = {"name": h, "host": h, "logo": "", "protected": False, "service": r.get("service") or ""}
    return {"apps": list(items.values())}


# Bekannte IDA-Apps nach Port: (Reihenfolge, Name, bevorzugte Adresse, wenn mehrere auf denselben Port zeigen)
IDA_APPS = {8440: ("IDA", "ida"), 8442: ("Kalender", "kalender"), 8443: ("Wiki", "wiki"), 8444: ("Code", "idacode"), 8449: ("Aufgaben", "tasks"),
            8451: ("Dokumente", "docs"), 8450: ("HTML", "html"), 8445: ("Dashboard", "dashboard"), 8446: ("Server", "server"),
            8448: ("Links", "link"), 8452: ("Launcher", "home")}
_ORDER = list(IDA_APPS)


def _port(service: str) -> int:
    try:
        return int((service or "").rsplit(":", 1)[1].split("/")[0])
    except (IndexError, ValueError):
        return 0


def _ports(spec: str) -> set[int]:
    out = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        if a.isdigit():
            out.update(range(int(a), int(b or a) + 1))
    return out


def group(items: list[dict]) -> list[dict]:
    """Setzt `ida` (bool) und für IDA-Apps einen schönen Namen. Pro Port bleibt nur eine Adresse (die bevorzugte) in der IDA-Gruppe."""
    s = get_settings()
    ports = _ports(s.ida_ports)
    best: dict[int, dict] = {}
    for a in items:
        p = _port(a.get("service", ""))
        a["ida"] = (p in ports and p != 0) or _match(a["host"], s.ida_hosts)
        a["order"] = _ORDER.index(p) if p in _ORDER else 100 + p
        if a["ida"] and p in ports:
            pref = IDA_APPS.get(p, ("", ""))[1]
            key = (not a["host"].startswith(pref + ".") if pref else False, not a["protected"], len(a["host"]), a["host"])
            cur = best.get(p)
            if not cur or key < cur[0]:
                best[p] = (key, a)
    keep = {id(v[1]) for v in best.values()}
    out = []
    for a in items:
        p = _port(a.get("service", ""))
        if a["ida"] and p in ports:
            if id(a) not in keep:
                continue  # zweite Adresse auf denselben IDA-Dienst (z. B. cleo und html)
            if p in IDA_APPS:
                a["name"] = IDA_APPS[p][0]
        out.append(a)
    return out


def _match(host: str, patterns: str) -> bool:
    return any(fnmatch(host, p.strip().lower()) for p in patterns.split(",") if p.strip())


async def apps(force: bool = False) -> list[dict]:
    s = get_settings()
    hit = _cache.get("apps")
    if hit and not force and time.monotonic() - hit[0] < s.cache_seconds:
        return hit[1]
    try:
        raw = (await fetch())["apps"]
    except CloudflareError:
        if hit:
            return hit[1]  # lieber die alte Liste als eine leere Seite
        raise
    out = [a for a in raw if not _match(a["host"], s.hide) and (a["protected"] or s.show_unprotected)]
    for part in s.extra.split(","):
        name, _, url = part.partition("=")
        if name.strip() and url.strip().startswith("http"):
            host = url.strip().split("//", 1)[1].split("/")[0].lower()
            out.append({"name": name.strip(), "host": host, "logo": "", "protected": True, "service": "", "url": url.strip()})
    out = group(out)
    out.sort(key=lambda a: (0, a["order"], a["host"]) if a["ida"] else (1, 0, a["name"].lower(), a["host"]))
    _cache["apps"] = (time.monotonic(), out)
    return out
