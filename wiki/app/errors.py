"""Fehler, die die API als {"detail": …} zurückgibt."""


class Invalid(Exception):
    """400 – Eingabe unbrauchbar oder Struktur-Regel verletzt."""

    def __init__(self, msg: str, extra: dict | None = None):
        super().__init__(msg)
        self.extra = extra or {}


class NotFound(Exception):
    """404 – Seite, Anhang oder Revision nicht gefunden."""


class Forbidden(Exception):
    """403 – keine Berechtigung (z. B. externes MCP will in privat/ schreiben)."""


class Conflict(Exception):
    """409 – die Seite wurde zwischenzeitlich geändert. `extra` liefert den aktuellen Serverstand."""

    def __init__(self, msg: str, extra: dict | None = None):
        super().__init__(msg)
        self.extra = extra or {}
