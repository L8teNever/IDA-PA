"""Cloudflare-API (nur lesend) mit demselben Token wie der Cloudflare-MCP im IDA-Stack (CLOUDFLARE_API_TOKEN).

Liefert zwei Dinge:
* Access-Apps (Zero Trust) samt Policies: daraus folgt sicher, ob eine öffentliche Adresse einen Login braucht, auch wenn
  nur ein Pfad (z. B. /admin) geschützt ist oder eine Policy „Bypass“ alles durchlässt.
* Ingress-Regeln der Tunnel (remote verwaltet): Hostname → Dienst, als zweite Quelle neben dem Metrics-Port von cloudflared.

Ohne Token oder bei Fehlern gibt es hier nichts, dann prüft access.py die Adressen wie bisher per Aufruf ohne Login.
"""
import logging
import time
from fnmatch import fnmatch
from urllib.parse import urlsplit

import httpx

from .config import get_settings

log = logging.getLogger("dashboard.cloudflare")

TTL = 120
_cache: dict[str, tuple[float, object]] = {}
_last_error = ""


class CloudflareError(Exception):
    pass


def enabled() -> bool:
    return bool(get_settings().cloudflare_api_token.strip())


async def _get(path: str, params: dict | None = None) -> object:
    return await _send("GET", path, params=params)


async def _send(method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> object:
    s = get_settings()
    try:
        async with httpx.AsyncClient(base_url=s.cloudflare_api_url.rstrip("/"), timeout=15,
                                     headers={"authorization": f"Bearer {s.cloudflare_api_token.strip()}"}) as c:
            r = await c.request(method, path, params=params, json=json)
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


async def _cached(key: str, fn):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    val = await fn()
    _cache[key] = (time.monotonic(), val)
    return val


async def account_id() -> str:
    own = get_settings().cloudflare_account_id.strip()
    if own:
        return own

    async def find():
        accs, _ = await _get("/accounts")
        if not accs:
            raise CloudflareError("Der Token sieht kein Konto (braucht Account-Rechte)")
        return accs[0]["id"]
    return await _cached("account", find)


def _app_view(a: dict) -> dict:
    doms = list(a.get("self_hosted_domains") or [])
    doms += [d.get("uri") for d in a.get("destinations") or [] if d.get("type", "public") == "public" and d.get("uri")]
    if a.get("domain"):
        doms.append(a["domain"])
    return {"id": a.get("id"), "name": a.get("name") or a.get("domain") or "?", "type": a.get("type"),
            "domains": list(dict.fromkeys(d.strip().lower() for d in doms if d)),
            "policies": [{"name": p.get("name") or "?", "decision": p.get("decision") or "?"} for p in a.get("policies") or []]}


async def access_apps() -> list[dict]:
    async def load():
        acc = await account_id()
        return [_app_view(a) for a in await _all(f"/accounts/{acc}/access/apps")]
    return await _cached("apps", load)


async def tunnel_routes() -> list[dict]:
    """Ingress-Regeln aller remote verwalteten Tunnel: [{host, service, path}]."""
    async def load():
        acc = await account_id()
        out = []
        for t in await _all(f"/accounts/{acc}/cfd_tunnel", {"is_deleted": "false"}):
            if t.get("remote_config") is False:
                continue  # lokal verwaltet (config.yml) – die Regeln kennt nur cloudflared selbst
            try:
                cfg, _ = await _get(f"/accounts/{acc}/cfd_tunnel/{t['id']}/configurations")
            except CloudflareError:
                continue
            for r in ((cfg or {}).get("config") or {}).get("ingress") or []:
                if r.get("hostname"):
                    out.append({"host": r["hostname"], "service": r.get("service") or "", "path": r.get("path"), "tunnel": t.get("name")})
        return out
    return await _cached("tunnels", load)


def _split(pattern: str) -> tuple[str, str]:
    host, _, path = pattern.partition("/")
    return host, "/" + path if path else ""


def _host_ok(pat: str, host: str) -> bool:
    return pat == host or fnmatch(host, pat)


def _path_ok(pat: str, path: str) -> bool:
    if not pat or pat in ("/", "/*"):
        return True
    return fnmatch(path, pat) or path == pat.rstrip("/") or path.startswith(pat.rstrip("*").rstrip("/") + "/")


def judge(url: str, apps: list[dict]) -> dict:
    """{state, detail, apps} für eine Adresse aus den Access-Apps."""
    u = urlsplit(url)
    host, path = (u.hostname or "").lower(), u.path or "/"
    best, best_len, partial = None, -1, []
    for a in apps:
        if a.get("type") not in (None, "self_hosted"):
            continue
        for d in a["domains"]:
            ph, pp = _split(d)
            if not _host_ok(ph, host):
                continue
            if _path_ok(pp, path):
                if len(d) > best_len:
                    best, best_len = a, len(d)
            else:
                partial.append(pp)
    if best:
        decisions = {p["decision"] for p in best["policies"]}
        names = ", ".join(p["name"] for p in best["policies"]) or "keine Policy"
        if decisions and decisions <= {"bypass"}:
            return {"state": "public", "detail": f"Access-App „{best['name']}“ lässt alle durch (Bypass: {names})", "app": best["name"]}
        return {"state": "access", "detail": f"Zero Trust: Access-App „{best['name']}“ (Policies: {names})", "app": best["name"]}
    if partial:
        return {"state": "public", "detail": "Offen – nur " + ", ".join(sorted(set(partial))) + " ist mit Access geschützt"}
    return {"state": "public", "detail": "Keine Access-App für diese Adresse – ohne Login erreichbar"}


async def access_for(urls: list[str]) -> dict[str, dict] | None:
    """URL → Ergebnis aus der API; None, wenn die API nicht geht (dann prüft access.py selbst)."""
    global _last_error
    if not enabled():
        return None
    try:
        apps = await access_apps()
    except CloudflareError as e:
        if str(e) != _last_error:
            log.warning("Access-Apps nicht lesbar: %s", e)
        _last_error = str(e)
        return None
    _last_error = ""
    return {u: {**judge(u, apps), "source": "api"} for u in urls}


async def status() -> dict:
    if not enabled():
        return {"enabled": False, "hinweis": "CLOUDFLARE_API_TOKEN fehlt – Schutz wird per Aufruf ohne Login geprüft"}
    out: dict = {"enabled": True}
    try:
        out["account_id"] = await account_id()
        out["access_apps"] = len(await access_apps())
    except CloudflareError as e:
        out["error"] = str(e)
        return out
    try:
        out["tunnel_routes"] = len(await tunnel_routes())
    except CloudflareError as e:
        out["tunnel_error"] = str(e)
    return out


def reset() -> None:
    _cache.clear()


# ---------------- Schreiben (nur IDA Server: Veröffentlichen, Websites) ----------------
# Braucht beim Token zusätzlich: Cloudflare Tunnel Edit, DNS Edit, Access: Apps and Policies Edit.

async def tunnels() -> list[dict]:
    acc = await account_id()
    return [{"id": t["id"], "name": t.get("name") or t["id"][:8], "status": t.get("status"), "remote": t.get("remote_config") is not False}
            for t in await _all(f"/accounts/{acc}/cfd_tunnel", {"is_deleted": "false"})]


async def tunnel_config(tunnel_id: str) -> dict:
    acc = await account_id()
    res, _ = await _get(f"/accounts/{acc}/cfd_tunnel/{tunnel_id}/configurations")
    return (res or {}).get("config") or {}


async def set_ingress(tunnel_id: str, rules: list[dict]) -> None:
    """Setzt die Regeln (ohne Catch-all); die alte Catch-all-Regel und alle übrigen Einstellungen bleiben."""
    cfg = await tunnel_config(tunnel_id)
    old = cfg.get("ingress") or []
    catchall = next((r for r in old if not r.get("hostname")), {"service": "http_status:404"})
    clean = [{k: v for k, v in r.items() if v not in (None, "")} for r in rules if r.get("hostname")]
    acc = await account_id()
    await _send("PUT", f"/accounts/{acc}/cfd_tunnel/{tunnel_id}/configurations", json={"config": {**cfg, "ingress": clean + [catchall]}})
    _cache.pop("tunnels", None)


async def add_route(tunnel_id: str, hostname: str, service: str, path: str = "") -> None:
    """Regel hostname(+path) → service; eine vorhandene Regel für genau diesen Host+Pfad wird ersetzt (vor allgemeineren)."""
    rules = [r for r in (await tunnel_config(tunnel_id)).get("ingress") or [] if r.get("hostname")]
    rules = [r for r in rules if not (r["hostname"] == hostname and (r.get("path") or "") == (path or ""))]
    new = {"hostname": hostname, "service": service, **({"path": path} if path else {})}
    # spezifischere Regeln (mit Pfad) vor die Regel desselben Hosts ohne Pfad, sonst greift die zuerst
    idx = next((i for i, r in enumerate(rules) if r["hostname"] == hostname and not r.get("path")), len(rules)) if path else len(rules)
    rules.insert(idx, new)
    await set_ingress(tunnel_id, rules)


async def remove_route(tunnel_id: str, hostname: str, path: str = "") -> bool:
    rules = [r for r in (await tunnel_config(tunnel_id)).get("ingress") or [] if r.get("hostname")]
    keep = [r for r in rules if not (r["hostname"] == hostname and (r.get("path") or "") == (path or ""))]
    if len(keep) == len(rules):
        return False
    await set_ingress(tunnel_id, keep)
    return True


async def zone_for(hostname: str) -> dict:
    zones = await _cached("zones", lambda: _all("/zones"))
    host = hostname.lower().rstrip(".")
    best = max((z for z in zones if host == z["name"] or host.endswith("." + z["name"])), key=lambda z: len(z["name"]), default=None)
    if not best:
        raise CloudflareError(f"Keine Cloudflare-Zone für {hostname} (Token sieht: {', '.join(z['name'] for z in zones) or 'keine'})")
    return best


async def point_to_tunnel(hostname: str, tunnel_id: str) -> str:
    """CNAME hostname → <tunnel>.cfargotunnel.com (proxied). Gibt created | updated | unchanged zurück."""
    z = await zone_for(hostname)
    target = f"{tunnel_id}.cfargotunnel.com"
    recs, _ = await _get(f"/zones/{z['id']}/dns_records", {"name": hostname})
    rec = next(iter(recs or []), None)
    body = {"type": "CNAME", "name": hostname, "content": target, "proxied": True, "ttl": 1, "comment": "IDA Server"}
    if rec and rec.get("type") != "CNAME":
        raise CloudflareError(f"Für {hostname} gibt es schon einen {rec.get('type')}-Eintrag ({rec.get('content')}) – erst in Cloudflare entfernen")
    if rec and rec.get("content") == target and rec.get("proxied"):
        return "unchanged"
    if rec:
        await _send("PUT", f"/zones/{z['id']}/dns_records/{rec['id']}", json=body)
        return "updated"
    await _send("POST", f"/zones/{z['id']}/dns_records", json=body)
    return "created"


async def remove_dns(hostname: str, tunnel_id: str) -> bool:
    """Löscht den CNAME nur, wenn er auf diesen Tunnel zeigt."""
    z = await zone_for(hostname)
    recs, _ = await _get(f"/zones/{z['id']}/dns_records", {"name": hostname, "type": "CNAME"})
    rec = next((r for r in recs or [] if r.get("content") == f"{tunnel_id}.cfargotunnel.com"), None)
    if not rec:
        return False
    await _send("DELETE", f"/zones/{z['id']}/dns_records/{rec['id']}")
    return True


def _include(emails: list[str]) -> list[dict]:
    out = []
    for e in emails:
        e = e.strip()
        if e.startswith("@") and len(e) > 1:
            out.append({"email_domain": {"domain": e[1:]}})
        elif "@" in e:
            out.append({"email": {"email": e}})
    return out


async def protect(domain: str, emails: list[str], name: str = "") -> dict:
    """Zero Trust für domain (host oder host/pfad): Access-App mit einer Allow-Policy für diese E-Mails (bzw. @domain).
    Gibt es schon eine App genau für diese Domain, bekommt sie die neue Policy statt einer zweiten App."""
    inc = _include(emails)
    if not inc:
        raise CloudflareError("Mindestens eine E-Mail-Adresse (oder @domain.de) angeben")
    acc = await account_id()
    domain = domain.strip().lower().rstrip("/")
    app = next((a for a in await access_apps() if domain in a["domains"]), None)
    if app:
        app_id = app["id"]
    else:
        res, _ = await _send("POST", f"/accounts/{acc}/access/apps", json={
            "name": name or domain, "domain": domain, "type": "self_hosted", "session_duration": "24h"})
        app_id = res["id"]
    await _send("POST", f"/accounts/{acc}/access/apps/{app_id}/policies", json={
        "name": "IDA Server: erlaubt", "decision": "allow", "include": inc, "precedence": 1})
    _cache.pop("apps", None)
    return {"app_id": app_id, "neu": not app}


async def unprotect(domain: str) -> bool:
    """Löscht die Access-App genau dieser Domain (danach ist sie ohne Login erreichbar)."""
    domain = domain.strip().lower().rstrip("/")
    app = next((a for a in await access_apps() if a["domains"] == [domain] or (a["domains"] and a["domains"][0] == domain)), None)
    if not app:
        return False
    acc = await account_id()
    await _send("DELETE", f"/accounts/{acc}/access/apps/{app['id']}")
    _cache.pop("apps", None)
    return True


def forget(*keys: str) -> None:
    for k in keys or list(_cache):
        _cache.pop(k, None)
