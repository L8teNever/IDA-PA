"""Neueste Version eines Images in seiner Registry nachsehen, ohne etwas zu laden.

Ein HEAD auf das Manifest reicht: Die Registry nennt den Digest, verglichen wird mit dem des laufenden Images. Das geht direkt statt
über den Docker-Daemon, weil HEAD bei Docker Hub nicht ins Pull-Limit zählt. Logins kommen aus ~/.docker/config.json (wie bei
`docker pull`); ein Credential-Helper wird nicht ausgewertet, das Image gilt dann als „kein Zugriff“.
"""
import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

HUB = "registry-1.docker.io"
ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))


class RegistryError(Exception):
    pass


class NotThere(RegistryError):
    """Die Registry kennt das Image nicht oder gibt es uns nicht."""


@dataclass(frozen=True)
class Ref:
    registry: str
    repo: str
    tag: str
    pinned: bool = False    # mit @sha256:… festgenagelt: dafür gibt es keine neuere Version


def parse(image: str) -> Ref:
    name, _, digest = image.partition("@")
    first, slash, rest = name.partition("/")
    if slash and ("." in first or ":" in first or first == "localhost"):
        registry, path = (HUB if first in ("docker.io", "index.docker.io") else first), rest
    else:
        registry, path = HUB, name
    path, colon, tag = path.rpartition(":") if ":" in path.rsplit("/", 1)[-1] else (path, "", "")
    if registry == HUB and "/" not in path:
        path = "library/" + path
    return Ref(registry, path, tag or "latest", bool(digest))


def host_of(key: str) -> str:
    host = urlsplit(key if "://" in key else "https://" + key).netloc.lower()
    return HUB if host in ("docker.io", "index.docker.io", "registry.hub.docker.com") else host


def load_auths(path: Path | None = None) -> dict[str, tuple[str, str]]:
    """Registry → (Benutzer, Passwort) aus der Docker-Konfiguration."""
    f = path or Path(os.environ.get("DOCKER_CONFIG") or Path.home() / ".docker") / "config.json"
    try:
        auths = json.loads(f.read_text(encoding="utf-8")).get("auths") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for key, v in auths.items():
        try:
            user, _, secret = base64.b64decode((v or {}).get("auth") or "").decode().partition(":")
        except (ValueError, AttributeError):
            continue
        if user:
            out[host_of(key)] = (user, secret)
    return out


def challenge(header: str) -> tuple[str, dict[str, str]]:
    scheme, _, rest = header.strip().partition(" ")
    return scheme.lower(), dict(re.findall(r'([a-zA-Z_]+)="([^"]*)"', rest))


async def token(client: httpx.AsyncClient, params: dict[str, str], ref: Ref, login: tuple[str, str] | None) -> str:
    if not params.get("realm"):
        raise RegistryError("Registry ohne Anmeldeadresse")
    q = {"scope": params.get("scope") or f"repository:{ref.repo}:pull"}
    if params.get("service"):
        q["service"] = params["service"]
    r = await client.get(params["realm"], params=q, auth=login)
    if r.status_code in (401, 403):
        raise NotThere("Kein Zugriff – privates Image ohne Login?")
    if r.status_code >= 400:
        raise RegistryError(f"Anmeldung bei der Registry fehlgeschlagen ({r.status_code})")
    try:
        data = r.json()
    except ValueError as e:
        raise RegistryError("Anmeldung bei der Registry fehlgeschlagen") from e
    t = data.get("token") or data.get("access_token")
    if not t:
        raise RegistryError("Registry hat keinen Token geliefert")
    return t


async def digest(client: httpx.AsyncClient, ref: Ref, login: tuple[str, str] | None = None) -> str:
    """Digest, den `docker pull` jetzt bekäme."""
    url = f"https://{ref.registry}/v2/{ref.repo}/manifests/{ref.tag}"
    try:
        r = await client.head(url, headers={"Accept": ACCEPT})
        if r.status_code == 401:
            scheme, params = challenge(r.headers.get("www-authenticate", ""))
            if scheme == "bearer":
                r = await client.head(url, headers={"Accept": ACCEPT, "Authorization": "Bearer " + await token(client, params, ref, login)})
            elif scheme == "basic" and login:
                r = await client.head(url, headers={"Accept": ACCEPT}, auth=login)
    except httpx.HTTPError as e:
        raise RegistryError(f"Registry nicht erreichbar ({type(e).__name__})") from e
    if r.status_code in (401, 403):
        raise NotThere("Kein Zugriff – privates Image ohne Login?")
    if r.status_code == 404:
        raise NotThere("In der Registry nicht gefunden")
    if r.status_code == 429:
        raise RegistryError("Registry bremst gerade (zu viele Anfragen)")
    if r.status_code >= 400:
        raise RegistryError(f"Registry antwortet mit Fehler {r.status_code}")
    d = r.headers.get("docker-content-digest", "")
    if not d.startswith("sha256:"):
        raise RegistryError("Registry nennt keinen Digest")
    return d
