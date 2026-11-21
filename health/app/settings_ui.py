"""Einstellungen im Browser: alles, was sonst in der .env stünde. Gespeichert in health.db (Tabelle settings), gilt sofort.
Reihenfolge: Standard < Umgebung (.env) < Browser. „Zurücksetzen“ nimmt wieder den Wert aus der .env bzw. den Standard.

Erste Einrichtung: Solange Health nicht abgesichert ist, kommt man nur mit dem Einrichtungs-Code hinein. Er steht beim Start im
Log des Dienstes und in data/setup-code.txt (wechselt bei jedem Start) und gilt danach als Notfall-Zugang weiter.
"""
import base64
import json
import logging
import secrets
import time

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import auth
from .config import env_settings, get_settings, set_overrides

log = logging.getLogger("health.settings")
router = APIRouter()

# key, Gruppe, Bezeichnung, Typ (text | secret | int | bool | url), Hilfe, (min, max) bei int
FIELDS = [
    ("public_url", "allgemein", "Öffentliche Adresse", "url", "So erreichst du Health von außen, z. B. https://health.kulbarts.com. Steht im Install-Befehl und gilt beim Koppeln.", None),
    ("install_url", "allgemein", "Install-Skript", "url", "Woher Geräte ida-connect laden (Standard: GitHub).", None),
    ("access_team", "zugang", "Cloudflare-Team", "text", "Der Teil vor .cloudflareaccess.com. Mit der AUD prüft Health die Anmeldung über Cloudflare Access selbst.", None),
    ("access_aud", "zugang", "Access-AUD", "text", "„Application Audience (AUD) Tag“ der Access-App von Health (Zero Trust → Access → Applications → Health → Overview).", None),
    ("ida_token", "zugang", "Token für IDA", "secret", "Damit bedient IDA (Ida, Code-Threads) die Geräte. Am einfachsten unten „Mit IDA verbinden“ – das erzeugt ihn und trägt ihn in IDA ein.", None),
    ("allow_open", "zugang", "Ohne Schutz offen", "bool", "Nur zum Testen: jeder, der die Adresse erreicht, hat Root-Zugriff auf alle Geräte.", None),
    ("cloudflare_api_token", "mesh", "Cloudflare-API-Token", "secret", "Nur für Cloudflare Mesh. Rechte: „Cloudflare One Connectors Write“ (Konto). Ohne Token: Mesh nur mit eigenem Knoten-Token.", None),
    ("cloudflare_account_id", "mesh", "Konto-ID", "text", "Leer = das erste Konto, das der Token sieht.", None),
    ("mesh_address", "mesh", "Adresse von Health im Mesh", "url", "Leer = automatisch (http://<Mesh-IP von Health>:8000). Geräte im Mesh verbinden sich darüber, sonst über die öffentliche Adresse.", None),
    ("mesh_node", "mesh", "Mesh-Knoten von Health", "text", "Setzt „Health ins Mesh aufnehmen“ selbst – nur ändern, wenn du weißt, was du tust.", None),
    ("mesh_ip", "mesh", "Mesh-IP von Health", "text", "Setzt Health selbst (zuletzt gesehene IP). Bleibt, auch wenn WARP kurz getrennt ist.", None),
    ("store_seconds", "messwerte", "Speichern alle … Sekunden", "int", "So oft landet ein Einzelwert in der Datenbank (live kommen sie öfter).", (2, 300)),
    ("raw_keep_hours", "messwerte", "Einzelwerte behalten (Stunden)", "int", "Danach bleiben nur die 5-Minuten-Mittel.", (1, 24 * 30)),
    ("keep_days", "messwerte", "5-Minuten-Mittel behalten (Tage)", "int", "Wie weit der Verlauf zurückreicht.", (1, 3650)),
    ("live_interval", "messwerte", "Live alle … Sekunden", "int", "So oft schickt ein Gerät, solange du es dir ansiehst.", (1, 60)),
    ("idle_interval", "messwerte", "Sonst alle … Sekunden", "int", "So oft schickt ein Gerät, wenn niemand zuschaut.", (2, 300)),
    ("ida_url", "ida", "Adresse von IDA (intern)", "url", "Im selben Docker-Stack: http://app:8000.", None),
    ("internal_url", "ida", "Adresse von Health (intern)", "url", "So erreicht IDA Health im Docker-Netz: http://health:8000.", None),
]
GROUPS = [("allgemein", "Allgemein", "tune"), ("zugang", "Zugang & Sicherheit", "shield_lock"), ("mesh", "Cloudflare Mesh", "hub"),
          ("messwerte", "Messwerte & Speicher", "monitoring"), ("ida", "Verbindung zu IDA", "link")]
META = {f[0]: f for f in FIELDS}

SETUP: dict = {"code": "", "sessions": {}, "fails": []}
SESSION_TTL = 12 * 3600
COOKIE = "hp_admin"


# ---------- Laden / Anwenden ----------
def apply(store) -> None:
    """Gespeicherte Werte übernehmen und an die laufenden Teile weitergeben (Speicher, Hub)."""
    set_overrides({k: v for k, v in store.overrides().items() if k in META})
    s = get_settings()
    store.store_seconds = s.store_seconds
    store.raw_keep = s.raw_keep_hours * 3600
    store.keep = s.keep_days * 86400


def tune_hub(hub) -> None:
    s = get_settings()
    hub.live_interval, hub.idle_interval = s.live_interval, s.idle_interval


def new_setup_code(data_dir) -> str:
    code = "-".join(secrets.token_hex(4) for _ in range(4))
    SETUP["code"] = code
    try:
        p = data_dir / "setup-code.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code + "\n", encoding="utf-8")
        p.chmod(0o600)
    except OSError:
        pass
    log.warning("Einrichtungs-Code für IDA Health (Browser → Einstellungen): %s", code)
    return code


def admin_session(cookies) -> bool:
    sid = cookies.get(COOKIE)
    t = SETUP["sessions"].get(sid) if sid else None
    return bool(t and time.time() - t < SESSION_TTL)


# ---------- Ansicht ----------
def _mask(v: str) -> str:
    return ("•••• " + v[-4:]) if len(v) > 8 else ("••••" if v else "")


def _view() -> dict:
    cur, env = get_settings(), env_settings()
    from .config import _overrides
    groups = []
    for gid, title, icon in GROUPS:
        fields = []
        for key, g, label, typ, help_, rng in FIELDS:
            if g != gid:
                continue
            v, e = getattr(cur, key), getattr(env, key)
            src = "browser" if key in _overrides else ("env" if e != env_default(key) else "standard")
            f = {"key": key, "label": label, "type": typ, "help": help_, "source": src, "min": rng[0] if rng else None, "max": rng[1] if rng else None}
            if typ == "secret":
                f.update({"set": bool(v), "hint": _mask(str(v))})
            else:
                f["value"] = v
            fields.append(f)
        groups.append({"id": gid, "title": title, "icon": icon, "fields": fields})
    return {"groups": groups, "secured": auth.secured(), "access_checked": bool(cur.access_team and cur.access_aud)}


def env_default(key: str):
    from .config import Settings
    return Settings.model_fields[key].default


# ---------- Routen ----------
class LoginIn(BaseModel):
    code: str


@router.post("/api/v1/setup/login")
async def setup_login(body: LoginIn, request: Request, response: Response):
    now = time.time()
    SETUP["fails"] = [t for t in SETUP["fails"] if now - t < 600]
    if len(SETUP["fails"]) >= 10:
        raise HTTPException(429, "Zu viele falsche Versuche – 10 Minuten warten.")
    if not SETUP["code"] or not secrets.compare_digest(body.code.strip().lower(), SETUP["code"]):
        SETUP["fails"].append(now)
        raise HTTPException(401, "Code falsch.")
    sid = secrets.token_urlsafe(32)
    SETUP["sessions"] = {k: t for k, t in SETUP["sessions"].items() if now - t < SESSION_TTL}
    SETUP["sessions"][sid] = now
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, sid, max_age=SESSION_TTL, httponly=True, samesite="strict", secure=https, path="/")
    return {"ok": True}


@router.post("/api/v1/setup/logout")
async def setup_logout(request: Request, response: Response):
    SETUP["sessions"].pop(request.cookies.get(COOKIE, ""), None)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


def _admin(request: Request) -> str:
    """Einstellungen: angemeldet (Access/Einrichtung), nicht per IDA-Token – IDA soll sich nicht selbst umkonfigurieren."""
    w = auth.require(request)
    if w == "ida":
        raise HTTPException(403, "Einstellungen nur im Browser")
    if not auth.same_origin(request):
        raise HTTPException(403, "Fremde Seite")
    return w


async def _ida_status() -> dict:
    """Ist Health in IDA eingetragen (gleiche Adresse, Token gesetzt)?"""
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(s.ida_url.rstrip("/") + "/api/v1/health-link")
        j = r.json()
        return {"reachable": True, "linked": bool(j.get("enabled") and j.get("token")), "url": j.get("url", "")}
    except (httpx.HTTPError, ValueError):
        return {"reachable": False, "linked": False, "url": ""}


@router.get("/api/v1/settings")
async def get_view(request: Request):
    _admin(request)
    return {**_view(), "ida": await _ida_status()}


class SaveIn(BaseModel):
    values: dict = {}
    reset: list[str] = []


def _coerce(key: str, v):
    _, _, label, typ, _, rng = META[key]
    if typ == "bool":
        return bool(v)
    if typ == "int":
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{label}: Zahl erwartet") from None
        if rng and not rng[0] <= n <= rng[1]:
            raise HTTPException(400, f"{label}: zwischen {rng[0]} und {rng[1]}")
        return n
    v = str(v or "").strip()
    if typ == "url" and v and not v.startswith(("http://", "https://")):
        raise HTTPException(400, f"{label}: muss mit http:// oder https:// beginnen")
    if key == "access_team":
        v = v.replace("https://", "").split(".cloudflareaccess.com")[0].strip("/")
    return v.rstrip("/") if typ == "url" else v


@router.put("/api/v1/settings")
async def save(body: SaveIn, request: Request):
    who = _admin(request)
    from .main import STATE
    unknown = [k for k in list(body.values) + body.reset if k not in META]
    if unknown:
        raise HTTPException(400, "Unbekannt: " + ", ".join(unknown))
    vals = {k: _coerce(k, v) for k, v in body.values.items() if not (META[k][3] == "secret" and v in (None, ""))}
    # Schutz vor dem Aussperren: Access nur, wenn Team und AUD zusammen gesetzt sind
    after = {**{k: getattr(get_settings(), k) for k in ("access_team", "access_aud")}, **{k: v for k, v in vals.items() if k in ("access_team", "access_aud")}}
    for k in body.reset:
        if k in after:
            after[k] = getattr(env_settings(), k)
    if bool(after["access_team"]) != bool(after["access_aud"]):
        raise HTTPException(400, "Cloudflare-Team und Access-AUD gehören zusammen – beide ausfüllen oder beide leer lassen.")
    STATE["store"].save_overrides(vals, body.reset)
    apply(STATE["store"])
    tune_hub(STATE["hub"])
    await STATE["hub"].tune()
    from . import selfmesh
    await STATE["hub"].set_mesh_server(selfmesh.address())
    changed = sorted(set(vals) | set(body.reset))
    STATE["store"].audit("health", who, "einstellungen", ", ".join(changed))
    return _view()


@router.post("/api/v1/settings/ida-token")
async def gen_ida_token(request: Request):
    """Neuen Token für IDA erzeugen (danach „Mit IDA verbinden“, sonst kennt IDA ihn nicht)."""
    who = _admin(request)
    from .main import STATE
    STATE["store"].save_overrides({"ida_token": secrets.token_hex(32)}, [])
    apply(STATE["store"])
    STATE["store"].audit("health", who, "einstellungen", "neuer IDA-Token")
    return _view()


@router.post("/api/v1/settings/link-ida")
async def link_ida(request: Request):
    """Trägt Adresse und Token von Health in IDA ein (IDA → /api/v1/health-link). Erzeugt den Token, falls noch keiner da ist."""
    who = _admin(request)
    from .main import STATE
    s = get_settings()
    if not s.ida_token:
        STATE["store"].save_overrides({"ida_token": secrets.token_hex(32)}, [])
        apply(STATE["store"])
        s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.put(s.ida_url.rstrip("/") + "/api/v1/health-link", json={"url": s.internal_url, "token": s.ida_token})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"IDA nicht erreichbar unter {s.ida_url}: {e}") from e
    if r.status_code >= 400:
        raise HTTPException(502, f"IDA antwortet {r.status_code}: {r.text[:200]}")
    STATE["store"].audit("health", who, "einstellungen", f"mit IDA verbunden ({s.ida_url})")
    return {"ok": True, "ida": r.json(), **_view()}


@router.post("/api/v1/settings/access-from-login")
async def access_from_login(request: Request):
    """Team und AUD aus der aktuellen Cloudflare-Anmeldung lesen (zum Übernehmen ins Formular – gespeichert wird erst beim Speichern)."""
    _admin(request)
    tok = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization")
    if not tok:
        raise HTTPException(400, "Keine Cloudflare-Access-Anmeldung in dieser Anfrage – öffne Health über die Adresse mit Access davor.")
    try:
        payload = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "=="))
    except (ValueError, IndexError):
        raise HTTPException(400, "Access-Anmeldung nicht lesbar") from None
    iss = str(payload.get("iss") or "")
    aud = payload.get("aud")
    aud = aud[0] if isinstance(aud, list) and aud else aud
    team = iss.replace("https://", "").split(".cloudflareaccess.com")[0]
    if not team or not aud:
        raise HTTPException(400, "In der Anmeldung stehen kein Team/AUD")
    # Prüfen, dass die Anmeldung mit diesen Werten wirklich gültig ist (Signatur des Teams)
    from .config import get_settings as gs
    s = gs()
    old = (s.access_team, s.access_aud)
    s.access_team, s.access_aud = team, str(aud)
    try:
        ok = auth.verify_access_jwt(tok)
    finally:
        s.access_team, s.access_aud = old
    if not ok:
        raise HTTPException(400, "Die Anmeldung ließ sich mit diesen Werten nicht prüfen.")
    return {"access_team": team, "access_aud": str(aud), "email": ok}


@router.post("/api/v1/settings/test-cloudflare")
async def test_cloudflare(request: Request):
    """Prüft den Mesh-Token: gültig? Konto? Darf er Mesh-Knoten (warp_connector) sehen?"""
    _admin(request)
    s = get_settings()
    if not s.cloudflare_api_token:
        raise HTTPException(400, "Kein Cloudflare-API-Token gespeichert")
    from . import mesh
    out = {"token": False, "account": "", "mesh": False, "detail": ""}
    try:
        async with mesh._client() as c:
            v = await c.get("/user/tokens/verify")
            out["token"] = v.status_code == 200 and (v.json().get("result") or {}).get("status") == "active"
            if not out["token"]:
                # Konto-Tokens prüft man unter /accounts/{id}/tokens/verify
                acc0 = s.cloudflare_account_id
                if acc0:
                    v2 = await c.get(f"/accounts/{acc0}/tokens/verify")
                    out["token"] = v2.status_code == 200
            acc = await mesh._account(c)
            out["account"] = acc
            r = await c.get(f"/accounts/{acc}/warp_connector", params={"per_page": 1})
            out["mesh"] = r.status_code == 200 and bool(r.json().get("success"))
            if not out["mesh"]:
                out["detail"] = "; ".join(str(e.get("message")) for e in (r.json().get("errors") or [])) or f"HTTP {r.status_code}"
    except (httpx.HTTPError, mesh.MeshError, ValueError) as e:
        out["detail"] = str(e)
    return out


# ---------- Health selbst im Mesh ----------
def _saver(who: str):
    from .main import STATE

    def save(values: dict) -> None:
        STATE["store"].save_overrides(values, [])
        apply(STATE["store"])
        STATE["store"].audit("health", who, "mesh", ", ".join(f"{k}={v or '–'}" for k, v in values.items()))
    return save


@router.get("/api/v1/settings/mesh-self")
async def mesh_self(request: Request):
    _admin(request)
    from . import selfmesh
    return await selfmesh.status()


@router.post("/api/v1/settings/mesh-self/join")
async def mesh_self_join(request: Request):
    """Health ins Mesh aufnehmen: Knoten anlegen, WARP im Container verbinden, Adresse an alle Geräte."""
    who = _admin(request)
    from . import mesh, selfmesh
    from .main import STATE
    if not mesh.enabled():
        raise HTTPException(400, "Erst oben den Cloudflare-API-Token speichern (Recht „Cloudflare One Connectors Write“).")
    st = await selfmesh.join(_saver(who))
    await STATE["hub"].set_mesh_server(selfmesh.address())
    return st


@router.post("/api/v1/settings/mesh-self/leave")
async def mesh_self_leave(request: Request):
    who = _admin(request)
    from . import selfmesh
    from .main import STATE
    st = await selfmesh.leave(_saver(who))
    await STATE["hub"].set_mesh_server(selfmesh.address())
    return st
