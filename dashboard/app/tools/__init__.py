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
    from . import files, monitor, stacks, storage  # Reihenfolge = Reihenfolge in der Leiste
    return [stacks.tool, files.tool, monitor.tool, storage.tool]
