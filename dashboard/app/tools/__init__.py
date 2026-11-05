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
        from . import monitor_proxy, publish, sites
        return [sites.tool, publish.tool, stacks.tool, files.tool, monitor_proxy.tool]
    from . import monitor, storage
    return [stacks.tool, files.tool, monitor.tool, storage.tool]


# Leiste der Oberfläche je Modus (ids der Tools in frontend/dashboard/js/tools/)
NAV = {"dashboard": ["overview", "stacks", "files", "monitor", "storage"], "server": ["sites", "publish", "stacks", "files"]}
