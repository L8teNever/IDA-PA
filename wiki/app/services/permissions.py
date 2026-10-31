"""Harte Rechte-Durchsetzung für die 3 Hauptordner (`privat`, `memory`, `ki`).

Diese Regeln gelten direkt im Wiki-Dienst – kein Client (weder externes MCP noch ein IDA-Agent) kann sie umgehen.
"""
from ..auth import Caller
from ..errors import Forbidden, Invalid
from ..models import SPACES

PRIVAT_ROOT_FOLDERS = (
    "00-inbox",
    "01-alltag-wohnen",
    "02-finanzen-steuern",
    "03-gesundheit-sport",
    "04-arbeit-projekte",
    "05-wissen-lernen",
    "06-menschen-beziehungen",
    "07-reisen-freizeit",
    "08-dokumente-vorlagen",
    "99-archiv",
)

PRIVAT_DEFAULT_FOLDERS = tuple(f"privat/{f}" for f in PRIVAT_ROOT_FOLDERS)

MEMORY_CATEGORIES = (
    "kern",
    "personen",
    "projekte",
    "konzepte-wissen",
    "entscheidungen",
    "agenten",
    "journal",
)


def space_of(path: str) -> str:
    clean = (path or "").strip().strip("/").replace("\\", "/")
    first = clean.split("/", 1)[0].lower()
    if first not in SPACES:
        raise Invalid(f"Ungültiger Hauptordner „{first or '?'}“ – erlaubt sind: {', '.join(SPACES)}")
    return first


def can_read_space(c: Caller, space: str) -> bool:
    if space not in SPACES:
        return False
    if c.kind in ("user", "ida", "ida_sync"):
        return True
    if c.kind == "mcp":
        if space == "memory":
            return True
        if space == "privat":
            return bool(c.allow_read_private)
        if space == "ki":
            return bool(c.allow_read_chats)
    return False


def require_read(c: Caller, space: str) -> None:
    if not can_read_space(c, space):
        raise Forbidden(f"Kein Lesezugriff auf den Bereich „{space}“")


def require_write(c: Caller, space: str, *, approved_proposal: bool = False) -> None:
    """Prüft Schreibzugriff (Anlegen, Ändern, Verschieben, Löschen, Anhänge)."""
    if space not in SPACES:
        raise Invalid(f"Unbekannter Bereich „{space}“")

    if space == "privat":
        if c.kind == "user":
            return
        if c.kind == "ida" and approved_proposal:
            return
        if c.kind == "ida":
            raise Forbidden(
                "Änderungen im privaten Bereich (`privat/`) erfordern die ausdrückliche Freigabe des Nutzers "
                "(nutze die `wiki_private_*`-Tools für eine Freigabe-Anfrage im Chat)."
            )
        raise Forbidden("Externe MCP-Zugänge haben im privaten Bereich (`privat/`) ausschließlich Lesezugriff.")

    if space == "memory":
        if c.kind in ("user", "ida", "mcp"):
            return
        raise Forbidden("Keine Schreibberechtigung für `memory/`")

    if space == "ki":
        if c.kind == "ida_sync":
            return
        raise Forbidden(
            "Der Bereich `ki/` wird automatisch aus IDA (Agents, Chats & Code-Threads) synchronisiert und ist schreibgeschützt. "
            "Du kannst aber von überall per [[ki/...]] darauf verlinken."
        )
