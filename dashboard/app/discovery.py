"""Stacks erkennen: laufende Compose-Projekte (Labels der Container), einzelne Container und Stack-Ordner auf der Platte."""
import os
import re
from pathlib import Path

import yaml

P = "com.docker.compose."
COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
SELF_LABEL = "ida.dashboard"


def ctr_name(c: dict) -> str:
    return (c.get("Names") or ["/" + c.get("Id", "")[:12]])[0].lstrip("/")


def stack_of(c: dict) -> str:
    """Stack eines Containers: das Compose-Projekt, sonst der Container selbst."""
    return (c.get("Labels") or {}).get(P + "project") or ctr_name(c)


def is_self(c: dict) -> bool:
    if (c.get("Labels") or {}).get(SELF_LABEL) == "self":
        return True
    host = os.environ.get("HOSTNAME", "")
    return len(host) >= 12 and c.get("Id", "").startswith(host)


def project_name(dirname: str) -> str:
    """Wie Compose den Projektnamen aus dem Ordner bildet."""
    return re.sub(r"[^a-z0-9_-]", "", dirname.lower()) or "default"


def health_of(status: str) -> str | None:
    m = re.search(r"\((healthy|unhealthy|health: starting)\)", status or "")
    return {"health: starting": "starting"}.get(m.group(1), m.group(1)) if m else None


def ports_of(c: dict) -> list[str]:
    seen, out = set(), []
    for p in c.get("Ports") or []:
        if not p.get("PublicPort"):
            continue
        s = f"{p['PublicPort']}→{p['PrivatePort']}" + ("/udp" if p.get("Type") == "udp" else "")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def ctr_view(c: dict) -> dict:
    lab = c.get("Labels") or {}
    return {"id": c["Id"], "name": ctr_name(c), "service": lab.get(P + "service") or ctr_name(c), "image": c.get("Image", ""),
            "state": c.get("State", ""), "status": c.get("Status", ""), "health": health_of(c.get("Status", "")),
            "created": c.get("Created"), "ports": ports_of(c), "self": is_self(c)}


def compose_files(d: Path) -> list[Path]:
    return [d / n for n in COMPOSE_NAMES if (d / n).is_file()][:1]


def declared_name(f: Path) -> str | None:
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        n = data.get("name") if isinstance(data, dict) else None
        return project_name(str(n)) if n and "$" not in str(n) else None
    except (OSError, yaml.YAMLError):
        return None


def discover(containers: list[dict], scan_dirs: list[Path]) -> list[dict]:
    stacks: dict[str, dict] = {}
    for c in containers:
        lab = c.get("Labels") or {}
        proj = lab.get(P + "project")
        key = proj or ctr_name(c)
        s = stacks.get(key)
        if not s:
            wd = lab.get(P + "project.working_dir") if proj else None
            cfg = [x for x in (lab.get(P + "project.config_files") or "").split(",") if x] if proj else []
            envs = [x for x in (lab.get(P + "project.environment_file") or "").split(",") if x] if proj else []
            s = stacks[key] = {"name": key, "kind": "compose" if proj else "container", "dir": wd, "config_files": cfg, "env_files": envs, "containers": []}
        s["containers"].append(ctr_view(c))
    known_dirs = {os.path.normpath(s["dir"]) for s in stacks.values() if s["dir"]}
    # Stack-Ordner, deren Container gerade nicht existieren (z. B. nach `down`)
    for root in scan_dirs:
        try:
            subdirs = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for d in subdirs:
            files = compose_files(d)
            if not files or os.path.normpath(str(d)) in known_dirs:
                continue
            name = declared_name(files[0]) or project_name(d.name)
            if name in stacks:
                continue
            stacks[name] = {"name": name, "kind": "compose", "dir": str(d), "config_files": [str(files[0])], "env_files": [], "containers": []}
    return [finish(s) for s in sorted(stacks.values(), key=lambda s: s["name"])]


def finish(s: dict) -> dict:
    cs = s["containers"]
    running = sum(c["state"] == "running" for c in cs)
    if not cs or not running:
        state = "stopped"
    elif any(c["health"] == "unhealthy" for c in cs) or any(c["state"] in ("restarting", "dead") for c in cs):
        state = "problem"
    elif running < len(cs):
        state = "partial"
    else:
        state = "running"
    files_ok = bool(s["dir"]) and bool(s["config_files"]) and all(os.path.isfile(f) for f in s["config_files"])
    cs.sort(key=lambda c: (c["service"], c["name"]))
    return {**s, "state": state, "running": running, "total": len(cs), "files_ok": files_ok, "self": any(c["self"] for c in cs)}


def stack_files(s: dict) -> list[dict]:
    """Dateien eines Stacks, die man bearbeiten darf: Compose-Dateien und .env."""
    out, seen = [], set()

    def add(path: str, kind: str):
        p = os.path.normpath(path)
        if p in seen:
            return
        seen.add(p)
        ok = os.path.isfile(p)
        out.append({"path": p, "name": os.path.basename(p), "kind": kind, "exists": ok, "size": os.path.getsize(p) if ok else 0})

    for f in s.get("config_files") or []:
        add(f, "compose")
    for f in s.get("env_files") or []:
        add(f, "env")
    if s.get("dir") and os.path.isdir(s["dir"]):
        add(os.path.join(s["dir"], ".env"), "env")
    return out
