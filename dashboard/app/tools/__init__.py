"""Tools des Dashboards.

Jedes Tool ist ein Modul mit einem `tool = Tool(...)`: eigener API-Router (unter /api/v1/...), optional ein
Hintergrund-Dienst (start/stop) – und in der Oberfläche eine Datei frontend/dashboard/js/tools/<id>.js.
Neues Tool: Modul hier anlegen, in TOOLS eintragen, Oberfläche in js/tools/ und in js/tools/index.js eintragen.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI


@dataclass
class Tool:
    id: str
    name: str
    icon: str                                              # Material-Symbol
    router: APIRouter
    start: Callable[[FastAPI], Awaitable[None]] | None = None
    stop: Callable[[FastAPI], Awaitable[None]] | None = None


def all_tools() -> list[Tool]:
    from ..config import get_settings
    from . import files, stacks  # Reihenfolge = Reihenfolge in der Leiste
    if get_settings().is_server:  # IDA Server: einrichten; Messwerte holt er beim Dashboard
        from . import links, monitor_proxy, sites, updates
        return [sites.tool, links.tool, stacks.tool, updates.tool, files.tool, monitor_proxy.tool]
    from . import graph, monitor, ports, publish, storage
    return [stacks.tool, graph.tool, publish.tool, ports.tool, files.tool, monitor.tool, storage.tool]


# Leiste der Oberfläche je Modus (ids der Tools in frontend/dashboard/js/tools/)
NAV = {"dashboard": ["overview", "stacks", "graph", "publish", "ports", "files", "monitor", "storage"], "server": ["sites", "links", "stacks", "updates", "files"]}
