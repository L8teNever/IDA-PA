"""musik_*-Tools des internen MCP: Spotify steuern (Ida im Chat, Stella und die Lautsprecher im Gespräch).

Im Gespräch über einen Lautsprecher leitet voice_bridge Befehle ohne Gerät direkt an diesen Lautsprecher (siehe speaker_command).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from .. import spotify

E = {"type": "string"}
ACTIONS = ["pause", "weiter", "naechstes", "vorheriges", "lauter", "leiser", "lautstaerke"]
GERAET = {**E, "description": "Spotify-Gerät, z. B. ein Lautsprecher („Nina Küche“) oder „Handy“; leer = wo gerade Musik läuft "
          "(im Gespräch am Lautsprecher: dieser Lautsprecher)"}


def spec() -> list[tuple[str, str, dict]]:
    obj = lambda props, req=(): {"type": "object", "properties": props, **({"required": list(req)} if req else {})}  # noqa: E731
    return [
        ("musik_spielen", "Spielt Musik von Spotify: sucht Lied, Album, Playlist, Künstler oder Podcast und startet es. "
                          "Direkt ausführen, wenn der Nutzer etwas hören will, ohne nachzufragen.",
         obj({"suche": {**E, "description": "Wonach gesucht wird, z. B. „Blinding Lights The Weeknd“ oder „Chill Playlist“"},
              "art": {"type": "string", "enum": list(spotify.KINDS), "description": "Standard lied"},
              "geraet": GERAET}, ("suche",))),
        ("musik_steuern", "Pause, weiter, nächstes/vorheriges Lied, lauter, leiser oder Lautstärke setzen (Spotify).",
         obj({"aktion": {"type": "string", "enum": ACTIONS},
              "wert": {"type": "integer", "minimum": 0, "maximum": 100, "description": "lautstaerke: Prozent; lauter/leiser: um wie viel (Standard 10)"},
              "geraet": GERAET}, ("aktion",))),
        ("musik_status", "Was läuft gerade auf Spotify, wo und wie laut; dazu alle Spotify-Geräte.", obj({})),
    ]


async def call(db: AsyncSession, me, name: str, a: dict) -> dict:  # noqa: ARG001
    device = str(a.get("geraet") or "").strip()
    if name == "musik_spielen":
        query = str(a.get("suche") or "").strip()
        if not query:
            return {"error": "suche fehlt"}
        return await spotify.play(query, str(a.get("art") or ""), device)
    if name == "musik_steuern":
        value = a.get("wert")
        return await spotify.control(str(a.get("aktion") or ""), int(value) if isinstance(value, (int, float)) else None, device)
    if name == "musik_status":
        return await spotify.now_playing()
    return {"error": f"Unbekanntes Tool {name}"}
