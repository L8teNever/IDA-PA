"""Tool „Websites“ (nur IDA Server): statische Seiten und SPAs hosten und direkt über den Tunnel veröffentlichen.

Aufbau (besser als das alte Carla-Hosting):
* Ein nginx-Dienst „sites“ im IDA-Stack (Label ida.sites=nginx) liefert alle Seiten aus: SITES_DIR/www/<name> → /srv/www/<name>,
  die Konfiguration schreibt der Server nach SITES_DIR/conf/default.conf. Vor dem Neuladen prüft `nginx -t` die neue
  Konfiguration; ist sie kaputt, kommt die alte zurück und nichts geht kaputt.
* Je Seite: Hostname und optional ein Pfad (host.de/blog), SPA-Modus (alle Routen → index.html), Quelle „Dateien“ (im Tool
  Dateien bearbeiten) oder ein GitHub-Repo (Branch, Ordner darin wie dist/, privates Repo per Token – der Token landet nie in
  .git), Auto-Update alle n Minuten, öffentlich oder Zero Trust (E-Mails).
* Anlegen, Aktualisieren und Löschen laufen als Vorgang mit Live-Ausgabe (wie die Stack-Aktionen).
* Veröffentlicht wird über die Cloudflare-API: Tunnel-Regel → nginx, DNS-CNAME, optional Access-App. Beim Löschen kommt nur
  weg, was die Seite selbst angelegt hat.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .. import cloudflare_api as cf
from .. import wiki_memory
from ..config import get_settings
from ..docker_api import DockerError
from ..jobs import Job
from . import Tool
from .publish import clean_host, clean_path, host_ip
from .stacks import docker_env, jobs

log = logging.getLogger("dashboard.sites")
router = APIRouter()

LABEL = "ida.sites"           # am nginx-Dienst: ida.sites=nginx
WWW_IN_NGINX = "/srv/www"     # so heißt SITES_DIR/www im nginx-Container
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
ROOT_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
REPO_RE = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9._/-]+?(\.git)?$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
_lock = asyncio.Lock()


# ---------------- Ablage ----------------
def www_dir() -> Path:
    return Path(get_settings().sites_dir) / "www"


def conf_file() -> Path:
    return Path(get_settings().sites_dir) / "conf" / "default.conf"


def meta_file() -> Path:
    return get_settings().data_dir / "sites.json"


def load() -> list[dict]:
    try:
        return json.loads(meta_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save(sites: list[dict]) -> None:
    f = meta_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(sites, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)  # enthält ggf. GitHub-Tokens
    os.replace(tmp, f)


def find(sites: list[dict], name: str) -> dict:
    s = next((x for x in sites if x["name"] == name), None)
    if not s:
        raise HTTPException(404, "Website nicht gefunden")
    return s


def normalize(name: str) -> str:
    n = (name or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), (" ", "-")):
        n = n.replace(a, b)
    n = re.sub(r"[^a-z0-9_-]+", "-", n)
    return re.sub(r"-+", "-", n).strip("-_")


def domains(s: dict) -> list[dict]:
    """Haupt-Adresse und weitere Domains, die auf dieselbe Seite zeigen."""
    return [{"hostname": s["hostname"], "path": s.get("path") or "", "main": True},
            *({**a, "path": a.get("path") or "", "main": False} for a in s.get("aliases") or [])]


def taken(sites: list[dict], host: str, path: str) -> dict | None:
    """Welche Website hat host+path schon (als Haupt-Adresse oder weitere Domain)?"""
    return next((s for s in sites for d in domains(s) if d["hostname"] == host and d["path"] == path), None)


def public(s: dict) -> dict:
    """Ohne Token für die Oberfläche."""
    out = {k: v for k, v in s.items() if k != "token"}
    out["has_token"] = bool(s.get("token"))
    out["url"] = f"https://{s['hostname']}{s.get('path') or ''}"
    out["domains"] = [{"hostname": d["hostname"], "path": d["path"], "main": d["main"], "url": f"https://{d['hostname']}{d['path']}"}
                      for d in domains(s)]
    out["folder"] = str(www_dir() / s["name"])
    return out


# ---------------- nginx ----------------
def nginx_conf(sites: list[dict]) -> str:
    lines = ["# vom IDA Server erzeugt – nicht von Hand ändern (Tool Websites)",
             "server {", "    listen 80 default_server;", "    server_name _;", "    return 404;", "}", ""]
    hosts: dict[str, list[tuple[dict, str]]] = {}
    for s in sites:
        for d in domains(s):
            hosts.setdefault(d["hostname"], []).append((s, d["path"]))
    for host, group in sorted(hosts.items()):
        lines += ["server {", "    listen 80;", f"    server_name {host};", "    charset utf-8;", "    server_tokens off;",
                  "    port_in_redirect off;", "    absolute_redirect off;",
                  "    location ~ /\\.(?!well-known) { return 404; }  # .git, .env … nie ausliefern", ""]
        root_site = None
        for s, p in sorted(group, key=lambda x: len(x[1]), reverse=True):
            base = f"{WWW_IN_NGINX}/{s['name']}" + (f"/{s['root']}" if s.get("root") else "")
            if not p:
                root_site = (s, base)
                continue
            fallback = f"{p}/index.html" if s.get("spa") else "=404"
            lines += [f"    location = {p} {{ return 302 {p}/$is_args$args; }}",
                      f"    location ^~ {p}/ {{", f"        alias {base}/;", "        index index.html index.htm;",
                      f"        try_files $uri $uri/ {fallback};", "    }", ""]
        if root_site:
            s, base = root_site
            fallback = "/index.html" if s.get("spa") else "=404"
            lines += [f"    root {base};", "    index index.html index.htm;", "    location / {", f"        try_files $uri $uri/ {fallback};", "    }",
                      "    location ~* \\.(css|js|mjs|png|jpe?g|gif|ico|svg|webp|avif|woff2?|ttf)$ {",
                      "        expires 7d;", '        add_header Cache-Control "public";', "        try_files $uri =404;", "    }"]
        else:
            lines += ["    location / { return 404; }"]
        lines += ["}", ""]
    return "\n".join(lines)


async def nginx_container(req_or_app) -> dict | None:
    docker = req_or_app.state.docker
    try:
        cs = await docker.containers()
    except DockerError:
        return None
    return next((c for c in cs if (c.get("Labels") or {}).get(LABEL) == "nginx"), None)


async def apply_conf(app, sites: list[dict], job: Job | None = None) -> None:
    """Neue Konfiguration schreiben, mit nginx -t prüfen, neu laden – bei Fehler die alte zurück."""
    from .stacks import run_exec

    f = conf_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    old = f.read_text(encoding="utf-8") if f.exists() else None
    f.write_text(nginx_conf(sites), encoding="utf-8")
    c = await nginx_container(app)
    if not c or c.get("State") != "running":
        if job:
            job.write("Hinweis: Der nginx-Dienst „sites“ läuft nicht – die Konfiguration gilt, sobald er startet.")
        return
    code, out, _ = await run_exec(["docker", "exec", c["Id"], "nginx", "-t"], 30)
    if code != 0:
        if old is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(old, encoding="utf-8")
        raise HTTPException(500, "nginx lehnt die neue Konfiguration ab, die alte bleibt: " + out.strip()[-500:])
    code, out, _ = await run_exec(["docker", "exec", c["Id"], "nginx", "-s", "reload"], 30)
    if job:
        job.write("nginx neu geladen ✓" if code == 0 else f"nginx neu laden: {out.strip()}")


async def upstream(app, routes: list[dict] | None = None) -> str:
    """Adresse, unter der der Tunnel den nginx der Websites erreicht."""
    own = get_settings().sites_upstream.strip()
    if own:
        return own.rstrip("/")
    c = await nginx_container(app)
    if c:
        for p in c.get("Ports") or []:
            if p.get("PrivatePort") == 80 and p.get("PublicPort"):
                ip = p.get("IP") or ""
                if ip in ("", "0.0.0.0", "::"):
                    ip = host_ip(routes or [])
                if ip and ip not in ("127.0.0.1", "::1") and ":" not in ip:
                    return f"http://{ip}:{p['PublicPort']}"
    raise HTTPException(400, "Weiß nicht, wie der Tunnel die Websites erreicht – SITES_UPSTREAM setzen (z. B. http://10.7.0.1:8087) "
                             "oder SITES_PORT auf eine IP binden, die cloudflared erreicht")


# ---------------- Git ----------------
def auth_url(repo: str, token: str) -> str:
    if not token:
        return repo
    u = urlsplit(repo)
    return f"{u.scheme}://x-access-token:{token}@{u.netloc}{u.path}"


def hide(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


async def git(job: Job | None, args: list[str], cwd: Path | None, token: str = "", timeout: int = 300) -> tuple[int, str]:
    env = {**docker_env(), "GIT_TERMINAL_PROMPT": "0"}
    proc = await asyncio.create_subprocess_exec("git", *args, cwd=str(cwd) if cwd else None, env=env,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        out, code = b"Abbruch: dauert zu lange", 124
    else:
        code = proc.returncode
    text = hide((out or b"").decode("utf-8", "replace"), token).strip()
    if job:
        job.write("$ git " + hide(" ".join(args), token))
        if text:
            job.write(text)
    return code, text


async def deploy(s: dict, job: Job | None = None) -> str:
    """Holt den Stand des Branches (frisch klonen oder fetch + reset). Gibt den Commit zurück."""
    d = www_dir() / s["name"]
    tok = s.get("token") or ""
    url = auth_url(s["repo"], tok)
    if not (d / ".git").exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists() and any(d.iterdir()):
            raise RuntimeError(f"{d} ist nicht leer und kein Git-Repo")
        code, out = await git(job, ["clone", "--depth", "1", "--branch", s["branch"], url, str(d)], None, tok)
        if code != 0:
            raise RuntimeError("Klonen ging nicht: " + out[-300:])
        await git(None, ["remote", "set-url", "origin", s["repo"]], d)  # ohne Token in .git/config
    else:
        code, out = await git(job, ["fetch", "--depth", "1", url, s["branch"]], d, tok)
        if code != 0:
            raise RuntimeError("Holen ging nicht: " + out[-300:])
        code, out = await git(job, ["reset", "--hard", "FETCH_HEAD"], d)
        if code != 0:
            raise RuntimeError(out[-300:])
        await git(job, ["clean", "-fdx"], d)
    _, sha = await git(None, ["rev-parse", "--short", "HEAD"], d)
    _, msg = await git(None, ["log", "-1", "--format=%s"], d)
    if s.get("root") and not (d / s["root"]).is_dir():
        if job:
            job.write(f"Achtung: Ordner {s['root']} gibt es im Repo nicht – die Seite zeigt 404, bis er existiert (z. B. nach dem Bauen).")
    return f"{sha.strip()} {msg.strip()}".strip()


WELCOME = """<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;color:#e3e3e3;
font:16px/1.5 system-ui,sans-serif}}main{{text-align:center;padding:32px}}h1{{font-weight:500;margin:0 0 8px}}p{{color:#9aa0a6}}</style></head>
<body><main><h1>{name}</h1><p>Läuft auf deinem Server. Die Dateien liegen in <code>{folder}</code> – im IDA Server unter Dateien bearbeiten.</p></main></body></html>
"""


# ---------------- API ----------------
class SiteIn(BaseModel):
    name: str = Field(max_length=60)
    hostname: str
    path: str = ""
    spa: bool = False
    source: str = "files"                  # files | github
    repo: str = ""
    branch: str = "main"
    root: str = ""                         # Ordner im Repo/Ordner, der ausgeliefert wird (z. B. dist)
    token: str = Field("", max_length=300)
    auto_minutes: int = Field(0, ge=0, le=1440)
    tunnel_id: str = ""
    zero_trust: bool = False
    emails: list[str] = Field(default_factory=list, max_length=50)


class SiteUpdate(BaseModel):
    spa: bool | None = None
    branch: str | None = None
    root: str | None = None
    token: str | None = Field(None, max_length=300)   # "" = Token entfernen
    auto_minutes: int | None = Field(None, ge=0, le=1440)
    zero_trust: bool | None = None
    emails: list[str] | None = Field(None, max_length=50)


def check_fields(root: str, branch: str, repo: str, source: str) -> None:
    if root and (not ROOT_RE.match(root) or ".." in root.split("/")):
        raise HTTPException(400, "Ordner nur mit Buchstaben, Zahlen, . _ - und /, z. B. dist oder build/web")
    if source == "github":
        if not REPO_RE.match(repo):
            raise HTTPException(400, "Repo als https-Adresse, z. B. https://github.com/L8teNever/seite")
        if not BRANCH_RE.match(branch):
            raise HTTPException(400, "Ungültiger Branch")


@router.get("/api/v1/sites")
async def list_sites(req: Request):
    c = await nginx_container(req.app)
    try:
        up = await upstream(req.app, await _routes())
        up_err = ""
    except HTTPException as e:
        up, up_err = "", e.detail
    return {"sites": [public(s) for s in load()], "nginx": {"found": bool(c), "state": c.get("State") if c else None, "name": c and c["Names"][0].lstrip("/")},
            "upstream": up, "upstream_error": up_err, "cloudflare": cf.enabled(), "dir": str(www_dir())}


async def _routes() -> list[dict]:
    if not cf.enabled():
        return []
    try:
        out = []
        for t in await cf.tunnels():
            if t["remote"]:
                out += [{"service": r.get("service") or ""} for r in (await cf.tunnel_config(t["id"])).get("ingress") or []]
        return out
    except cf.CloudflareError:
        return []


@router.get("/api/v1/sites/tunnels")
async def site_tunnels():
    if not cf.enabled():
        return {"tunnels": []}
    try:
        return {"tunnels": [t for t in await cf.tunnels() if t["remote"]]}
    except cf.CloudflareError as e:
        raise HTTPException(502, str(e))


@router.post("/api/v1/sites")
async def create_site(body: SiteIn, req: Request):
    name = normalize(body.name)
    if not NAME_RE.match(name):
        raise HTTPException(400, "Name fehlt (Buchstaben, Zahlen, - und _)")
    host, path = clean_host(body.hostname), clean_path(body.path).rstrip("/")
    if path and not re.match(r"^/[A-Za-z0-9._~/-]+$", path):
        raise HTTPException(400, "Pfad nur mit Buchstaben, Zahlen und . _ ~ - /")
    source = "github" if body.source == "github" else "files"
    root, branch, repo = body.root.strip().strip("/"), (body.branch or "main").strip(), body.repo.strip()
    check_fields(root, branch, repo, source)
    if body.zero_trust and not [e for e in body.emails if "@" in e]:
        raise HTTPException(400, "Für Zero Trust mindestens eine E-Mail (oder @domain.de) angeben")
    sites = load()
    if any(s["name"] == name for s in sites):
        raise HTTPException(409, f"Es gibt schon eine Website „{name}“")
    if other := taken(sites, host, path):
        raise HTTPException(409, f"{host}{path} gehört schon zur Website „{other['name']}“")
    if cf.enabled() and not body.tunnel_id:
        raise HTTPException(400, "Tunnel wählen")
    site = {"name": name, "hostname": host, "path": path, "spa": body.spa, "source": source, "repo": repo if source == "github" else "",
            "branch": branch if source == "github" else "", "root": root, "token": body.token.strip() if source == "github" else "",
            "auto_minutes": body.auto_minutes if source == "github" else 0, "tunnel_id": body.tunnel_id, "zero_trust": body.zero_trust,
            "emails": [e.strip() for e in body.emails if e.strip()], "created": int(time.time()), "status": "setting_up",
            "owns_route": False, "owns_dns": False, "owns_access": False, "last_commit": "", "last_deploy": 0, "last_error": "", "aliases": []}
    async with _lock:
        sites = load()
        sites.append(site)
        save(sites)

    async def work(job: Job) -> bool:
        ok = await _setup(req.app, site, job)
        wiki_memory.kick(req.app)
        return ok

    job = jobs(req).start(f"Website anlegen: {name}", "site:" + name, work)
    return {"job": job.view(), "site": public(site)}


async def _update(name: str, **changes) -> dict:
    async with _lock:
        sites = load()
        s = find(sites, name)
        s.update(changes)
        save(sites)
        return s


async def _setup(app, site: dict, job: Job) -> bool:
    name = site["name"]
    d = www_dir() / name
    try:
        if site["source"] == "github":
            commit = await deploy(site, job)
            await _update(name, last_commit=commit, last_deploy=int(time.time()))
            job.write(f"Stand: {commit} ✓")
        else:
            d.mkdir(parents=True, exist_ok=True)
            target = d / site["root"] if site["root"] else d
            target.mkdir(parents=True, exist_ok=True)
            if not (target / "index.html").exists():
                (target / "index.html").write_text(WELCOME.format(name=name, folder=target), encoding="utf-8")
            job.write(f"Ordner {target} bereit ✓")
        await apply_conf(app, load(), job)
        if cf.enabled():
            await _publish(app, site, job)
        await _update(name, status="ok", last_error="")
        job.write(f"Fertig: https://{site['hostname']}{site['path']}")
        return True
    except (RuntimeError, HTTPException, cf.CloudflareError, OSError) as e:
        msg = getattr(e, "detail", None) or str(e)
        job.write(f"Fehler: {msg}")
        await _update(name, status="error", last_error=str(msg)[:500])
        return False


async def _publish_domain(app, tid: str, host: str, path: str, site: dict, job: Job) -> dict:
    """Eine Adresse über den Tunnel veröffentlichen (Tunnel-Regel, DNS, ggf. Access). Gibt zurück, was dabei neu angelegt wurde."""
    rules = [r for r in (await cf.tunnel_config(tid)).get("ingress") or [] if r.get("hostname")]
    up = await upstream(app, rules)
    same_host = [r for r in rules if r["hostname"] == host]
    owns = {}
    if any(r.get("service", "").rstrip("/") == up and not r.get("path") for r in same_host):
        job.write(f"Tunnel: {host} zeigt schon auf die Websites ✓")
    elif not same_host:
        await cf.add_route(tid, host, up)
        owns["owns_route"] = True
        owns["route_path"] = ""
        job.write(f"Tunnel-Regel {host} → {up} ✓")
    elif path:
        rpath = f"^{re.escape(path)}(/.*)?$"
        await cf.add_route(tid, host, up, rpath)
        owns["owns_route"] = True
        owns["route_path"] = rpath
        job.write(f"Tunnel-Regel {host}{path} → {up} ✓ (der Rest von {host} bleibt beim bisherigen Ziel)")
    else:
        raise RuntimeError(f"{host} zeigt im Tunnel schon auf {same_host[0].get('service')} – anderen Hostnamen oder einen Pfad nehmen")
    dns = await cf.point_to_tunnel(host, tid)
    if dns == "created":
        owns["owns_dns"] = True
    job.write({"created": f"DNS {host}: CNAME angelegt ✓", "updated": f"DNS {host}: CNAME auf den Tunnel umgestellt ✓",
               "unchanged": f"DNS {host} passt schon ✓"}[dns])
    if site["zero_trust"]:
        res = await cf.protect(host + path, site["emails"], name=f"Website {site['name']}")
        owns["owns_access"] = res["neu"]
        job.write(f"Zero Trust {host}{path}: nur {', '.join(site['emails'])} ✓")
    else:
        job.write(f"Öffentlich: {host}{path} ohne Login erreichbar")
    return owns


async def _unpublish_domain(tid: str, host: str, path: str, owns: dict, rest: list[dict], job: Job) -> None:
    """Nur entfernen, was diese Adresse selbst angelegt hat. rest = alle Websites ohne die entfernte Adresse."""
    others = [d for s in rest for d in domains(s) if d["hostname"] == host]
    if owns.get("owns_route") and (owns.get("route_path") or not others):
        await cf.remove_route(tid, host, owns.get("route_path") or "")
        job.write(f"Tunnel-Regel {host}{path} entfernt ✓")
    left = [r for r in (await cf.tunnel_config(tid)).get("ingress") or [] if r.get("hostname") == host]
    if owns.get("owns_dns") and not left and not others:
        job.write(f"DNS-Eintrag {host} gelöscht ✓" if await cf.remove_dns(host, tid) else f"DNS {host}: nichts zu löschen")
    if owns.get("owns_access"):
        job.write(f"Access-App {host}{path} gelöscht ✓" if await cf.unprotect(host + path) else f"Access-App {host}{path} gab es nicht mehr")


async def _publish(app, site: dict, job: Job) -> None:
    owns = await _publish_domain(app, site["tunnel_id"], site["hostname"], site["path"], site, job)
    await _update(site["name"], **owns)


# ---------------- weitere Domains ----------------
class DomainIn(BaseModel):
    hostname: str
    path: str = ""


def clean_domain(body: DomainIn) -> tuple[str, str]:
    host, path = clean_host(body.hostname), clean_path(body.path).rstrip("/")
    if not host or "." not in host:
        raise HTTPException(400, "Adresse fehlt, z. B. seite.kulbarts.com")
    if path and not re.match(r"^/[A-Za-z0-9._~/-]+$", path):
        raise HTTPException(400, "Pfad nur mit Buchstaben, Zahlen und . _ ~ - /")
    return host, path


@router.post("/api/v1/sites/{name}/domains")
async def add_domain(name: str, body: DomainIn, req: Request):
    s = find(load(), name)
    host, path = clean_domain(body)
    if other := taken(load(), host, path):
        raise HTTPException(409, f"{host}{path} gehört schon zur Website „{other['name']}“")
    if cf.enabled() and not s.get("tunnel_id"):
        raise HTTPException(400, "Die Website hat keinen Tunnel – sie lässt sich nur über Cloudflare veröffentlichen")
    if jobs(req).busy("site:" + name):
        raise HTTPException(409, "Für diese Website läuft schon ein Vorgang")

    async def work(job: Job) -> bool:
        async with _lock:
            sites = load()
            cur = find(sites, name)
            cur.setdefault("aliases", []).append({"hostname": host, "path": path})
            save(sites)
        try:
            await apply_conf(req.app, load(), job)
            owns = await _publish_domain(req.app, cur["tunnel_id"], host, path, cur, job) if cf.enabled() else {}
        except (RuntimeError, HTTPException, cf.CloudflareError, OSError) as e:
            job.write(f"Fehler: {getattr(e, 'detail', None) or e}")
            async with _lock:  # halb angelegte Domain wieder raus, damit nichts ins Leere zeigt
                sites = load()
                cur = find(sites, name)
                cur["aliases"] = [a for a in cur.get("aliases") or [] if not (a["hostname"] == host and (a.get("path") or "") == path)]
                save(sites)
            try:
                await apply_conf(req.app, load())
            except HTTPException:
                pass
            return False
        async with _lock:
            sites = load()
            for a in find(sites, name).get("aliases") or []:
                if a["hostname"] == host and (a.get("path") or "") == path:
                    a.update(owns)
            save(sites)
        job.write(f"Fertig: https://{host}{path} zeigt jetzt auch auf {name}")
        wiki_memory.kick(req.app)
        return True

    return {"job": jobs(req).start(f"Domain hinzufügen: {host}{path}", "site:" + name, work).view()}


@router.post("/api/v1/sites/{name}/domains/remove")
async def remove_domain(name: str, body: DomainIn, req: Request):
    s = find(load(), name)
    host, path = clean_host(body.hostname), clean_path(body.path).rstrip("/")
    alias = next((a for a in s.get("aliases") or [] if a["hostname"] == host and (a.get("path") or "") == path), None)
    if not alias:
        raise HTTPException(404 if (host, path) != (s["hostname"], s.get("path") or "") else 400,
                            "Die Haupt-Adresse lässt sich nicht einzeln entfernen" if (host, path) == (s["hostname"], s.get("path") or "")
                            else "Diese Domain gehört nicht zur Website")
    if jobs(req).busy("site:" + name):
        raise HTTPException(409, "Für diese Website läuft schon ein Vorgang")

    async def work(job: Job) -> bool:
        async with _lock:
            sites = load()
            cur = find(sites, name)
            cur["aliases"] = [a for a in cur.get("aliases") or [] if not (a["hostname"] == host and (a.get("path") or "") == path)]
            save(sites)
        ok = True
        try:
            await apply_conf(req.app, load(), job)
        except HTTPException as e:
            job.write(f"nginx: {e.detail}")
            ok = False
        if cf.enabled() and s.get("tunnel_id"):
            try:
                await _unpublish_domain(s["tunnel_id"], host, path, alias, load(), job)
            except cf.CloudflareError as e:
                job.write(f"Cloudflare: {e}")
                ok = False
        job.write(f"{host}{path} zeigt nicht mehr auf {name}")
        wiki_memory.kick(req.app)
        return ok

    return {"job": jobs(req).start(f"Domain entfernen: {host}{path}", "site:" + name, work).view()}


@router.post("/api/v1/sites/{name}/deploy")
async def deploy_now(name: str, req: Request):
    s = find(load(), name)
    if s["source"] != "github":
        raise HTTPException(400, "Diese Website hat kein Repo – die Dateien bearbeitest du im Tool Dateien")
    if jobs(req).busy("site:" + name):
        raise HTTPException(409, "Für diese Website läuft schon ein Vorgang")

    async def work(job: Job) -> bool:
        try:
            commit = await deploy(s, job)
        except RuntimeError as e:
            job.write(f"Fehler: {e}")
            await _update(name, last_error=str(e)[:500])
            return False
        await _update(name, last_commit=commit, last_deploy=int(time.time()), last_error="")
        job.write(f"Stand: {commit} ✓")
        return True

    return {"job": jobs(req).start(f"Website aktualisieren: {name}", "site:" + name, work).view()}


@router.put("/api/v1/sites/{name}")
async def update_site(name: str, body: SiteUpdate, req: Request):
    s = find(load(), name)
    ch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "root" in ch:
        ch["root"] = ch["root"].strip().strip("/")
    check_fields(ch.get("root", s["root"]), ch.get("branch", s["branch"] or "main"), s["repo"], s["source"])
    if "emails" in ch:
        ch["emails"] = [e.strip() for e in ch["emails"] if e.strip()]
    if "token" in ch:
        ch["token"] = ch["token"].strip()
    zt_on = ch.get("zero_trust", s["zero_trust"])
    emails = ch.get("emails", s["emails"])
    if zt_on and not [e for e in emails if "@" in e]:
        raise HTTPException(400, "Für Zero Trust mindestens eine E-Mail (oder @domain.de) angeben")
    detail = []
    if cf.enabled() and ("zero_trust" in ch or ("emails" in ch and zt_on)):
        aliases = [dict(a) for a in s.get("aliases") or []]
        try:
            # gleich für die Haupt-Adresse (Felder direkt an der Website) und jede weitere Domain
            for d, owner in [(s["hostname"] + (s.get("path") or ""), s), *((a["hostname"] + (a.get("path") or ""), a) for a in aliases)]:
                owned = owner.get("owns_access", False)
                if zt_on:
                    if s["zero_trust"] and owned:
                        await cf.unprotect(d)  # alte Policy weg, neue mit den aktuellen E-Mails
                    res = await cf.protect(d, emails, name=f"Website {name}")
                    new_owned = res["neu"] or owned
                elif s["zero_trust"]:
                    if owned:
                        await cf.unprotect(d)
                    else:
                        detail.append(f"Die Access-App für {d} gehört nicht zu dieser Website – in Cloudflare entfernen")
                    new_owned = False
                else:
                    new_owned = owned
                if owner is s:
                    ch["owns_access"] = new_owned
                else:
                    owner["owns_access"] = new_owned
            if aliases:
                ch["aliases"] = aliases
            detail.insert(0, "Zero Trust an" if zt_on else "Zero Trust aus" if s["zero_trust"] else "")
            detail = [x for x in detail if x]
        except cf.CloudflareError as e:
            raise HTTPException(502, str(e))
    s = await _update(name, **ch)
    if {"spa", "root"} & ch.keys():
        await apply_conf(req.app, load())
    wiki_memory.kick(req.app)
    return {"site": public(s), "detail": ", ".join(detail)}


class RemoveIn(BaseModel):
    delete_files: bool = False


@router.post("/api/v1/sites/{name}/remove")
async def remove_site(name: str, body: RemoveIn, req: Request):
    s = find(load(), name)

    async def work(job: Job) -> bool:
        ok = True
        async with _lock:
            rest = [x for x in load() if x["name"] != name]
            save(rest)
        try:
            await apply_conf(req.app, rest, job)
        except HTTPException as e:
            job.write(f"nginx: {e.detail}")
            ok = False
        if cf.enabled() and s.get("tunnel_id"):
            todo = [(s["hostname"], s.get("path") or "", s), *((a["hostname"], a.get("path") or "", a) for a in s.get("aliases") or [])]
            for i, (host, path, owns) in enumerate(todo):
                # „Rest“ = andere Websites plus die noch nicht entfernten Domains dieser Seite
                still = [{"hostname": h, "path": p} for h, p, _ in todo[i + 1:]]
                rest_now = rest + ([{"hostname": still[0]["hostname"], "path": still[0]["path"], "aliases": still[1:]}] if still else [])
                try:
                    await _unpublish_domain(s["tunnel_id"], host, path, owns, rest_now, job)
                except cf.CloudflareError as e:
                    job.write(f"Cloudflare {host}: {e}")
                    ok = False
        if body.delete_files:
            d = www_dir() / name
            if d.is_dir() and d.resolve().parent == www_dir().resolve():
                shutil.rmtree(d, ignore_errors=True)
                job.write(f"Ordner {d} gelöscht ✓")
        else:
            job.write(f"Die Dateien bleiben in {www_dir() / name}")
        wiki_memory.kick(req.app)
        return ok

    return {"job": jobs(req).start(f"Website entfernen: {name}", "site:" + name, work).view()}


# ---------------- Auto-Update ----------------
async def _auto_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for s in load():
            m = s.get("auto_minutes") or 0
            if s.get("source") != "github" or not m or now - (s.get("last_deploy") or 0) < m * 60:
                continue
            if app.state.jobs.busy("site:" + s["name"]):
                continue
            try:
                commit = await deploy(s)
                if commit != s.get("last_commit"):
                    log.info("Website %s aktualisiert: %s", s["name"], commit)
                await _update(s["name"], last_commit=commit, last_deploy=int(time.time()), last_error="")
            except (RuntimeError, HTTPException, OSError) as e:
                await _update(s["name"], last_deploy=int(time.time()), last_error=f"Auto-Update: {e}"[:500])


async def start(app: FastAPI) -> None:
    try:  # erster Start: Konfiguration (nur der 404-Server) anlegen, damit der nginx „sites“ sauber läuft
        (www_dir()).mkdir(parents=True, exist_ok=True)
        if not conf_file().exists():
            await apply_conf(app, load())
    except (OSError, HTTPException) as e:
        log.warning("Websites: Konfiguration nicht angelegt: %s", getattr(e, "detail", e))
    app.state.sites_task = asyncio.create_task(_auto_loop(app))


async def stop(app: FastAPI) -> None:
    t = getattr(app.state, "sites_task", None)
    if t:
        t.cancel()


tool = Tool(id="sites", name="Websites", icon="language", router=router, start=start, stop=stop)

