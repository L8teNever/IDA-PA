"""Terminfarben (für den dunklen Hintergrund abgestimmt). Ein Test prüft den Kontrast der Textfarbe."""

ON = "#1b1b1f"
BASE = "#121316"

# key: (Name, fill)
_COLORS = {
    "rot": ("Rot", "#f28b82"),
    "koralle": ("Koralle", "#f6aea9"),
    "orange": ("Orange", "#fbbc6e"),
    "gelb": ("Gelb", "#fde293"),
    "salbei": ("Salbei", "#b5d6a7"),
    "gruen": ("Grün", "#81c995"),
    "tuerkis": ("Türkis", "#78d9ec"),
    "blau": ("Blau", "#8ab4f8"),
    "lavendel": ("Lavendel", "#c3b9f5"),
    "lila": ("Lila", "#d7aefb"),
    "graphit": ("Graphit", "#b4b8c0"),
}


def _mix(fill: str, base: str, amount: float) -> str:
    f = [int(fill[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(b[i] + (f[i] - b[i]) * amount):02x}" for i in range(3))


def palette() -> dict:
    """fill = Termin-Block, container = gedämpft (vergangen/abgesagt), on = Textfarbe auf fill."""
    return {k: {"name": n, "fill": f, "container": _mix(f, BASE, 0.28), "on": ON} for k, (n, f) in _COLORS.items()}


KEYS = list(_COLORS)


def luminance(hex_color: str) -> float:
    def ch(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
