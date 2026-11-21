"""Docker und Updates auf einem Gerät – über exec des Agenten (Befehle werden hier sicher zusammengesetzt, nie aus Rohtext)."""
import json
import shlex

from .hub import AgentError, Hub

STACK_ACTIONS = {"update": "pull && up -d", "up": "up -d", "restart": "restart", "stop": "stop", "start": "start", "down": "down"}
CTR_ACTIONS = ("start", "stop", "restart")

SYSTEM_UPDATE = r"""set -e
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get -y -o Dpkg::Options::=--force-confold upgrade && apt-get -y autoremove
elif command -v dnf >/dev/null 2>&1; then dnf -y upgrade
elif command -v apk >/dev/null 2>&1; then apk update && apk upgrade
elif command -v pacman >/dev/null 2>&1; then pacman -Syu --noconfirm
else echo "Kein bekannter Paketmanager" >&2; exit 2; fi
[ -f /var/run/reboot-required ] && echo "NEUSTART NÖTIG" || true
"""

UPDATES_CHECK = r"""if command -v apt-get >/dev/null 2>&1; then apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null | grep -c '^Inst ' || true
elif command -v dnf >/dev/null 2>&1; then dnf -q check-update 2>/dev/null | grep -c '\.' || true
elif command -v apk >/dev/null 2>&1; then apk version -l '<' 2>/dev/null | tail -n +2 | wc -l
else echo -1; fi; [ -f /var/run/reboot-required ] && echo reboot || true"""


def _check(r: dict) -> dict:
    if r.get("code") != 0:
        raise AgentError((r.get("stderr") or r.get("stdout") or "Befehl fehlgeschlagen").strip()[:4000])
    return r


def _json_lines(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("["):
        try:
            return json.loads(text)
        except ValueError:
            return []
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


async def docker_overview(hub: Hub, dev: str) -> dict:
    r = await hub.exec(dev, "command -v docker >/dev/null 2>&1 || { echo NODOCKER; exit 0; }; "
                            "docker compose ls -a --format json 2>/dev/null; echo '---'; docker ps -a --format '{{json .}}'", 40)
    out = r.get("stdout") or ""
    if out.strip() == "NODOCKER":
        return {"docker": False, "stacks": [], "containers": []}
    _check(r)
    a, _, b = out.partition("---")
    stacks = [{"name": s.get("Name"), "status": s.get("Status"), "files": [f for f in (s.get("ConfigFiles") or "").split(",") if f]}
              for s in _json_lines(a)]
    ctrs = [{"name": c.get("Names"), "image": c.get("Image"), "state": c.get("State"), "status": c.get("Status"), "ports": c.get("Ports"),
             "stack": _label(c.get("Labels") or "", "com.docker.compose.project"), "service": _label(c.get("Labels") or "", "com.docker.compose.service")}
            for c in _json_lines(b)]
    return {"docker": True, "stacks": stacks, "containers": ctrs}


def _label(labels: str, key: str) -> str:
    for kv in labels.split(","):
        k, _, v = kv.partition("=")
        if k == key:
            return v
    return ""


async def _stack(hub: Hub, dev: str, name: str) -> dict:
    ov = await docker_overview(hub, dev)
    s = next((s for s in ov["stacks"] if s["name"] == name), None)
    if not s:
        raise AgentError(f"Stack {name} gibt es auf dem Gerät nicht" + (" – vorhanden: " + ", ".join(x["name"] for x in ov["stacks"]) if ov["stacks"] else ""))
    return s


async def stack_action(hub: Hub, dev: str, name: str, action: str) -> dict:
    if action not in STACK_ACTIONS:
        raise AgentError("Aktion unbekannt: " + ", ".join(STACK_ACTIONS))
    s = await _stack(hub, dev, name)
    files = " ".join(f"-f {shlex.quote(f)}" for f in s["files"])
    workdir = shlex.quote(s["files"][0].rsplit("/", 1)[0]) if s["files"] else "/"
    base = f"docker compose -p {shlex.quote(name)} {files}"
    steps = " && ".join(f"{base} {part.strip()}" for part in STACK_ACTIONS[action].split("&&"))
    r = await hub.exec(dev, f"cd {workdir} && {steps} 2>&1", 900)
    return {"ok": r.get("code") == 0, "output": (r.get("stdout") or "")[-20000:]}


async def stack_files(hub: Hub, dev: str, name: str) -> list[str]:
    s = await _stack(hub, dev, name)
    d = s["files"][0].rsplit("/", 1)[0] if s["files"] else ""
    extra = [f"{d}/.env"] if d else []
    return s["files"] + extra


async def container_action(hub: Hub, dev: str, name: str, action: str) -> dict:
    if action not in CTR_ACTIONS:
        raise AgentError("Aktion unbekannt: " + ", ".join(CTR_ACTIONS))
    r = await hub.exec(dev, f"docker {action} {shlex.quote(name)} 2>&1", 120)
    return {"ok": r.get("code") == 0, "output": r.get("stdout") or ""}


async def container_logs(hub: Hub, dev: str, name: str, tail: int = 200) -> str:
    tail = max(10, min(int(tail or 200), 5000))
    r = await hub.exec(dev, f"docker logs --tail {tail} --timestamps {shlex.quote(name)} 2>&1", 40)
    return (r.get("stdout") or "")[-200000:]


async def updates_check(hub: Hub, dev: str) -> dict:
    r = await hub.exec(dev, UPDATES_CHECK, 120)
    lines = (r.get("stdout") or "").split()
    try:
        n = int(lines[0]) if lines else -1
    except ValueError:
        n = -1
    return {"pending": n, "reboot": "reboot" in lines}


async def system_update(hub: Hub, dev: str) -> dict:
    r = await hub.exec(dev, SYSTEM_UPDATE + " 2>&1", 3000)
    out = r.get("stdout") or ""
    return {"ok": r.get("code") == 0, "reboot": "NEUSTART NÖTIG" in out, "output": out[-30000:]}
