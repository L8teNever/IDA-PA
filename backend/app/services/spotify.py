"""Spotify über die offizielle Web-API: Lied suchen, auf einem Gerät abspielen, Pause/Weiter/Lautstärke.

Geräte sind alles, was Spotify Connect kennt – auch die Lautsprecher (ida-speaker, librespot), die sich in der Spotify-App
als eigenes Gerät zeigen. Anmeldung einmal im Browser (/api/v1/spotify/login); der Refresh-Token liegt in den Einstellungen.
"""
import base64
import secrets
import time
from urllib.parse import urlencode

import httpx

from ..config import get_settings
from ..db import session_scope
from . import settings_store
from .errors import Invalid

KEY = "spotify_auth"
# Suche braucht seit 2026 user-read-private; eigene Playlists und Lieblingssongs: playlist-read-*, user-library-read
SCOPES = ("user-read-private user-read-playback-state user-modify-playback-state user-read-currently-playing "
          "playlist-read-private playlist-read-collaborative user-library-read")
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"

KINDS = {"lieblingssongs": "collection", "lied": "track", "album": "album", "playlist": "playlist", "kuenstler": "artist", "podcast": "show"}

_states: dict[str, float] = {}  # Anmeldung läuft: state -> Ablauf
_access: dict = {}  # {"token", "until"} im Speicher


def configured() -> bool:
    s = get_settings()
    return bool(s.spotify_client_id and s.spotify_client_secret)


def redirect_uri(base: str) -> str:
    return base.rstrip("/") + "/api/v1/spotify/callback"


def login_url(base: str) -> str:
    if not configured():
        raise Invalid("Spotify ist nicht eingerichtet (SPOTIFY_CLIENT_ID und SPOTIFY_CLIENT_SECRET in der .env).")
    now = time.time()
    for k, exp in list(_states.items()):
        if exp < now:
            _states.pop(k, None)
    state = secrets.token_urlsafe(24)
    _states[state] = now + 600
    q = {"client_id": get_settings().spotify_client_id, "response_type": "code", "redirect_uri": redirect_uri(base),
         "scope": SCOPES, "state": state, "show_dialog": "true"}
    return "https://accounts.spotify.com/authorize?" + urlencode(q)


def _basic() -> str:
    s = get_settings()
    return "Basic " + base64.b64encode(f"{s.spotify_client_id}:{s.spotify_client_secret}".encode()).decode()


async def _token_call(data: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(TOKEN_URL, data=data, headers={"Authorization": _basic()})
    except httpx.HTTPError as e:
        raise Invalid("Spotify ist gerade nicht erreichbar.") from e
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400:
        raise Invalid(f"Spotify-Anmeldung: {body.get('error_description') or body.get('error') or r.status_code}")
    return body


async def finish_login(code: str, state: str, base: str) -> None:
    if _states.pop(state or "", 0) < time.time():
        raise Invalid("Anmeldung abgelaufen – bitte nochmal starten.")
    body = await _token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(base)})
    if not body.get("refresh_token"):
        raise Invalid("Spotify hat keinen Refresh-Token geschickt.")
    async with session_scope() as db:
        await settings_store.put(db, KEY, {"refresh_token": body["refresh_token"]})
    _access.update(token=body.get("access_token") or "", until=time.time() + int(body.get("expires_in") or 3600) - 60)


async def logged_in() -> bool:
    async with session_scope() as db:
        return bool((await settings_store.get(db, KEY, {}) or {}).get("refresh_token"))


async def logout() -> None:
    async with session_scope() as db:
        await settings_store.delete(db, KEY)
    _access.clear()


async def _token() -> str:
    if _access.get("token") and _access.get("until", 0) > time.time():
        return _access["token"]
    if not configured():
        raise Invalid("Spotify ist nicht eingerichtet (SPOTIFY_CLIENT_ID und SPOTIFY_CLIENT_SECRET in der .env).")
    async with session_scope() as db:
        saved = await settings_store.get(db, KEY, {}) or {}
    if not saved.get("refresh_token"):
        raise Invalid("Spotify ist noch nicht angemeldet – einmal <IDA>/api/v1/spotify/login im Browser öffnen.")
    body = await _token_call({"grant_type": "refresh_token", "refresh_token": saved["refresh_token"]})
    if body.get("refresh_token") and body["refresh_token"] != saved["refresh_token"]:
        async with session_scope() as db:
            await settings_store.put(db, KEY, {"refresh_token": body["refresh_token"]})
    _access.update(token=body["access_token"], until=time.time() + int(body.get("expires_in") or 3600) - 60)
    return _access["token"]


async def _api(method: str, path: str, **kw) -> dict:
    token = await _token()
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.request(method, API + path, headers={"Authorization": f"Bearer {token}"}, **kw)
    except httpx.HTTPError as e:
        raise Invalid("Spotify ist gerade nicht erreichbar.") from e
    if r.status_code == 401:
        _access.clear()
    if r.status_code >= 400:
        try:
            msg = (r.json().get("error") or {}).get("message") or r.text
        except ValueError:
            msg = r.text
        if r.status_code == 404 and "device" in str(msg).lower():
            msg = "Kein aktives Gerät – nenne einen Lautsprecher oder starte Spotify irgendwo einmal."
        elif r.status_code == 403 and "scope" in str(msg).lower():
            msg = f"Der Anmeldung fehlt eine Berechtigung – einmal neu anmelden: {(get_settings().ida_public_url or '<IDA>').rstrip('/')}/api/v1/spotify/login"
        elif r.status_code == 403:
            msg = f"Spotify erlaubt das nicht (Premium nötig?): {msg}"
        raise Invalid(f"Spotify: {str(msg)[:200]}")
    if r.status_code == 204 or not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {}


async def devices() -> list[dict]:
    data = await _api("GET", "/me/player/devices")
    return [{"id": d.get("id"), "name": d.get("name"), "typ": d.get("type"), "aktiv": bool(d.get("is_active")),
             "lautstaerke": d.get("volume_percent")} for d in data.get("devices") or [] if d.get("id")]


def norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss") if ch.isalnum())


def pick_device(devs: list[dict], name: str) -> dict | None:
    """Gerät nach Namen: genau, sonst enthalten (ohne Groß/Klein, Umlaute, Leerzeichen). Leer = das aktive Gerät."""
    if not name.strip():
        return next((d for d in devs if d["aktiv"]), None)
    want = norm(name)
    return (next((d for d in devs if norm(d["name"]) == want), None)
            or next((d for d in devs if want and (want in norm(d["name"]) or norm(d["name"]) in want)), None))


async def _device_id(name: str) -> str | None:
    if not name.strip():
        return None
    devs = await devices()
    d = pick_device(devs, name)
    if not d:
        names = ", ".join(x["name"] for x in devs) or "keins"
        raise Invalid(f"Gerät „{name}“ gibt es bei Spotify gerade nicht. Da sind: {names}.")
    return d["id"]


LIKED_WORDS = ("lieblingssong", "lieblingslied", "lieblingsmusik", "likedsongs", "gelikt", "gelike", "favoriten", "meinesongs", "meinemusik")


def wants_liked(query: str, kind: str = "") -> bool:
    q = norm(query)
    return kind == "lieblingssongs" or any(w in q for w in LIKED_WORDS)


def _playlist_name(query: str) -> str:
    """„die Playlist Chill“ / „meine Chill-Playlist“ -> „Chill“."""
    words = [w for w in query.replace("-", " ").split() if norm(w) not in ("die", "meine", "mein", "playlist", "liste", "von", "mir")]
    return " ".join(words) or query


async def own_playlist(query: str) -> dict | None:
    """Eigene (und gefolgte) Playlists: genauer Name, sonst enthalten. None = keine passende."""
    want = norm(_playlist_name(query))
    if not want:
        return None
    found: list[dict] = []
    for offset in range(0, 300, 50):
        data = await _api("GET", "/me/playlists", params={"limit": 50, "offset": offset})
        items = [i for i in data.get("items") or [] if i]
        found += items
        if len(items) < 50:
            break
    exact = next((p for p in found if norm(p.get("name", "")) == want), None)
    hit = exact or next((p for p in found if want in norm(p.get("name", "")) or norm(p.get("name", "")) in want and norm(p.get("name", ""))), None)
    return {"uri": hit["uri"], "titel": f"deine Playlist {hit.get('name')}"} if hit and hit.get("uri") else None


async def search(query: str, kind: str = "") -> dict:
    """Bestes Ergebnis -> {"uri", "titel"}. kind: lieblingssongs, lied, album, playlist, kuenstler, podcast (leer = Lied).
    Lieblingssongs -> gelikte Songs; Playlist -> zuerst die eigenen Playlists, dann die Spotify-Suche."""
    if wants_liked(query, kind):
        me = await _api("GET", "/me")
        if not me.get("id"):
            raise Invalid("Spotify: Konto nicht gefunden")
        return {"uri": f"spotify:user:{me['id']}:collection", "titel": "deine Lieblingssongs"}
    if kind == "playlist" or "playlist" in query.lower():
        mine = await own_playlist(query)
        if mine:
            return mine
        kind, query = "playlist", _playlist_name(query)
    t = KINDS.get(kind, "track")
    data = await _api("GET", "/search", params={"q": query[:200], "type": t, "limit": 1, "market": "from_token"})
    items = ((data.get(t + "s") or {}).get("items") or [])
    item = next((i for i in items if i), None)
    if not item:
        raise Invalid(f"Zu „{query}“ habe ich bei Spotify nichts gefunden.")
    who = ", ".join(a.get("name", "") for a in item.get("artists") or [])
    if t == "album" and who:
        title = f"{item.get('name')} von {who}"
    elif t == "playlist":
        title = f"Playlist {item.get('name')}"
    else:
        title = f"{item.get('name')}" + (f" von {who}" if who else "")
    return {"uri": item.get("uri"), "titel": title}


async def play(query: str, kind: str = "", device: str = "") -> dict:
    hit = await search(query, kind)
    dev = await _device_id(device)
    body = {"uris": [hit["uri"]]} if hit["uri"].startswith(("spotify:track:", "spotify:episode:")) else {"context_uri": hit["uri"]}
    await _api("PUT", "/me/player/play", params={"device_id": dev} if dev else None, json=body)
    return {"result": f"Spielt: {hit['titel']}" + (f" auf {device}" if device else "")}


async def transfer(device: str) -> dict:
    """Holt die Wiedergabe (was gerade läuft oder pausiert ist, z. B. am PC) auf das Gerät und spielt dort an derselben Stelle weiter."""
    if not device.strip():
        raise Invalid("Auf welches Gerät? geraet fehlt.")
    dev = await _device_id(device)
    await _api("PUT", "/me/player", json={"device_ids": [dev], "play": True})
    return {"result": f"Spielt jetzt auf {device} weiter"}


async def control(action: str, value: int | None = None, device: str = "") -> dict:
    if action == "uebernehmen":
        return await transfer(device)
    dev = await _device_id(device)
    p = {"device_id": dev} if dev else {}
    if action in ("weiter", "play"):
        await _api("PUT", "/me/player/play", params=p)
    elif action in ("pause", "stopp"):
        await _api("PUT", "/me/player/pause", params=p)
    elif action == "naechstes":
        await _api("POST", "/me/player/next", params=p)
    elif action == "vorheriges":
        await _api("POST", "/me/player/previous", params=p)
    elif action in ("lauter", "leiser", "lautstaerke"):
        if action == "lautstaerke":
            if value is None:
                raise Invalid("wert (0–100) fehlt")
            vol = value
        else:
            now = await _api("GET", "/me/player")
            cur = int(((now or {}).get("device") or {}).get("volume_percent") or 50)
            step = value or 10
            vol = cur + step if action == "lauter" else cur - step
        await _api("PUT", "/me/player/volume", params={**p, "volume_percent": max(0, min(100, int(vol)))})
    else:
        raise Invalid(f"Unbekannte Aktion {action}")
    return {"result": "ok"}


async def now_playing() -> dict:
    data = await _api("GET", "/me/player")
    if not data:
        return {"result": "Gerade läuft nichts.", "geraete": await devices()}
    item = data.get("item") or {}
    who = ", ".join(a.get("name", "") for a in item.get("artists") or [])
    return {"laeuft": bool(data.get("is_playing")), "titel": item.get("name"), "kuenstler": who,
            "geraet": (data.get("device") or {}).get("name"), "lautstaerke": (data.get("device") or {}).get("volume_percent"),
            "geraete": await devices()}
