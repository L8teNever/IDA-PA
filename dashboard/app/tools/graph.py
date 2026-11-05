"""Tool „Graph“ (Dashboard): alles, was auf dem Server läuft, als Netz – wie der Wissensgraph im Wiki.

Punkte: Stacks, ihre Container, die öffentlichen Adressen (Tunnel-Routen) und die Zero-Trust-Apps (Cloudflare Access),
dazu die Tunnel. Verbindungen: Stack → Container → Adresse ← Access-App, Adresse → Tunnel. Welche Access-App eine Adresse
schützt, kommt aus der Cloudflare-API (wie beim Schutz der URLs); ohne Token fehlen nur die Apps.
"""
from urllib.parse import urlsplit

from fastapi import APIRouter, Request

from .. import cloudflare_api, tunnels
from ..config import get_settings
from ..discovery import ctr_name, discover
from ..docker_api import DockerError
from . import Tool

router = APIRouter()


@router.get("/api/v1/graph")
async def graph(req: Request):
    docker = req.app.state.docker
    try:
        cs = await docker.containers()
    except DockerError as e:
        return {"nodes": [], "edges": [], "error": str(e)}
    stacks = discover(cs, get_settings().stack_dirs)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set = set()

    def node(nid: str, **kw) -> str:
        nodes.setdefault(nid, {"id": nid, **kw})
        return nid

    def edge(a: str, b: str, kind: str = "") -> None:
        if a != b and (a, b) not in seen_edges:
            seen_edges.add((a, b))
            edges.append({"source": a, "target": b, **({"kind": kind} if kind else {})})

    stack_of: dict[str, str] = {}
    for s in stacks:
        sid = node(f"s:{s['name']}", kind="stack", label=s["name"], state=s["state"], stack=s["name"],
                   sub=f"{s['running']}/{s['total']} laufen" if s["total"] else "aus")
        for c in s["containers"]:
            stack_of[c["id"]] = s["name"]
            cid = node(f"c:{c['id']}", kind="container", label=c["service"], state=c["state"], health=c.get("health"),
                       stack=s["name"], sub=f"{c['name']} · {c.get('image', '')}")
            edge(sid, cid)

    # öffentliche Adressen aus den Tunneln
    try:
        routes = await tunnels.routes(docker, cs)
    except DockerError:
        routes = []
    idx = tunnels.indexes(cs)
    apps: list[dict] = []
    cf_error = ""
    if cloudflare_api.enabled():
        try:
            apps = await cloudflare_api.access_apps()
        except cloudflare_api.CloudflareError as e:
            cf_error = str(e)

    def domain(url: str, **kw) -> str:
        u = urlsplit(url)
        label = (u.hostname or "") + (u.path if u.path not in ("", "/") else "")
        return node(f"d:{label.lower()}", kind="domain", label=label, url=url, **kw)

    for r in routes:
        url = f"https://{r['host']}{r.get('path') or ''}"
        did = domain(url, service=r["service"])
        c = tunnels.target_of(r["service"], idx)
        if c:
            edge(f"c:{c['Id']}", did)
        if r.get("tunnel"):
            edge(did, node(f"t:{r['tunnel']}", kind="tunnel", label=r["tunnel"], sub="Cloudflare-Tunnel"), "tunnel")

    # Zero-Trust-Apps: je App ein Punkt, verbunden mit den Adressen, die sie schützt
    for a in apps:
        if a.get("type") not in (None, "self_hosted"):
            continue
        decisions = {p["decision"] for p in a["policies"]}
        node(f"a:{a['id']}", kind="access", label=a["name"], bypass=bool(decisions) and decisions <= {"bypass"},
             sub=", ".join(p["name"] for p in a["policies"]) or "keine Policy", domains=a["domains"])
        for d in a["domains"]:
            if "*" in d:
                continue
            nid = f"d:{d}"
            if nid not in nodes:  # App ohne Tunnel-Route hier (anderer Server, nur ein Pfad …)
                domain(f"https://{d}", orphan=True)
                host = d.split("/", 1)[0]
                if "/" in d and f"d:{host}" in nodes:
                    edge(f"d:{host}", nid, "path")
    for n in [n for n in nodes.values() if n["kind"] == "domain"]:
        j = cloudflare_api.judge(n["url"], apps) if apps else None
        n["access"] = j["state"] if j else "unknown"
        n["detail"] = j["detail"] if j else ("Cloudflare-API nicht verfügbar" if cf_error or not cloudflare_api.enabled() else "")
        if j and j.get("app_id") and f"a:{j['app_id']}" in nodes:
            edge(f"a:{j['app_id']}", n["id"], "access")

    counts = {}
    for n in nodes.values():
        counts[n["kind"]] = counts.get(n["kind"], 0) + 1
    return {"nodes": list(nodes.values()), "edges": edges, "counts": counts,
            "cloudflare": cloudflare_api.enabled(), "error": cf_error}


tool = Tool(id="graph", name="Graph", icon="hub", router=router)
