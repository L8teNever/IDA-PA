"""Werte des ganzen Servers aus /proc (vom Host eingebunden unter /host/proc) und der Platte (/hostfs)."""
import os
import re
from pathlib import Path

# virtuelle Karten: deren Verkehr läuft ohnehin über die echte Karte (sonst doppelt gezählt)
VIRTUAL = re.compile(r"^(lo|veth|br-|docker|virbr|vnet|wg|tailscale|tun|tap|zt|cni|flannel|cali|kube|vxlan|genev|dummy|bond-slave)")


def proc_dir(configured: str) -> Path:
    p = Path(configured)
    return p if (p / "stat").is_file() else Path("/proc")


def read_cpu(proc: Path) -> tuple[int, int, int]:
    """(belegt, gesamt, Kerne) in Ticks seit Start."""
    lines = (proc / "stat").read_text().splitlines()
    v = [int(x) for x in lines[0].split()[1:]]
    idle = v[3] + (v[4] if len(v) > 4 else 0)
    total = sum(v[:8])  # guest steckt schon in user
    cores = sum(1 for ln in lines if re.match(r"cpu\d+ ", ln))
    return total - idle, total, cores


def read_mem(proc: Path) -> dict:
    m = {}
    for ln in (proc / "meminfo").read_text().splitlines():
        k, _, rest = ln.partition(":")
        m[k] = int(rest.split()[0]) * 1024 if rest.strip() else 0
    total = m.get("MemTotal", 0)
    avail = m.get("MemAvailable", m.get("MemFree", 0) + m.get("Cached", 0))
    return {"mem": total - avail, "mem_total": total, "swap": m.get("SwapTotal", 0) - m.get("SwapFree", 0), "swap_total": m.get("SwapTotal", 0)}


def read_load(proc: Path) -> float:
    try:
        return float((proc / "loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def read_uptime(proc: Path) -> float:
    try:
        return float((proc / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def net_file(proc: Path) -> Path:
    # /proc/1/net/dev = Netz des Servers (PID 1 des Hosts); /proc/net/dev wäre nur das Netz dieses Containers
    p = proc / "1" / "net" / "dev"
    try:
        p.read_text()
        return p
    except OSError:
        return proc / "net" / "dev"


def read_net(path: Path, only: list[str]) -> tuple[int, int, list[str]]:
    rx = tx = 0
    used = []
    for ln in path.read_text().splitlines()[2:]:
        name, _, rest = ln.partition(":")
        name = name.strip()
        if only and name not in only:
            continue
        if not only and VIRTUAL.match(name):
            continue
        f = rest.split()
        if len(f) < 9:
            continue
        rx += int(f[0])
        tx += int(f[8])
        used.append(name)
    return rx, tx, used


def read_disk(root: str) -> dict:
    path = root if os.path.isdir(root) else "/"
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    return {"disk_used": used, "disk_total": total, "disk_free": free}
