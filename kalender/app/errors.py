"""Fehler, die die API als {"detail": …} zurückgibt."""


class Invalid(Exception):
    """400 – Eingabe unbrauchbar."""


class NotFound(Exception):
    """404"""


class Forbidden(Exception):
    """403 – z. B. ein Agent schreibt in einen Kalender, der für Agents nur lesbar ist."""


class Conflict(Exception):
    """409 – der Termin wurde zwischenzeitlich geändert."""
