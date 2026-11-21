"""Cloudflare Mesh: Knoten für ein Gerät anlegen (Token geht dann an den Agenten, der warp-cli einrichtet) und wieder löschen.
API: POST /accounts/{id}/warp_connector {name} -> id, GET …/warp_connector/{id}/token -> Token. Braucht „Cloudflare One Connectors Write“.
"""
import httpx

from .config import get_settings


class MeshError(Exception):
    pass


def enabled() -> bool:
    s = get_settings()
    return bool(s.cloudflare_api_token.strip())


async def _account(c: httpx.AsyncClient) -> str:
    s = get_settings()
    if s.cloudflare_account_id:
        return s.cloudflare_account_id
    r = await c.get("/accounts", params={"per_page": 5})
    items = r.json().get("result") or []
    if not items:
        raise MeshError("Cloudflare-Konto nicht gefunden (CLOUDFLARE_ACCOUNT_ID setzen)")
    return items[0]["id"]


def _client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(base_url=s.cloudflare_api_url, timeout=20, headers={"authorization": f"Bearer {s.cloudflare_api_token}"})


def _ok(r: httpx.Response) -> dict:
    try:
        j = r.json()
    except ValueError:
        raise MeshError(f"Cloudflare antwortet {r.status_code}") from None
    if not j.get("success"):
        errs = "; ".join(str(e.get("message")) for e in j.get("errors") or []) or f"HTTP {r.status_code}"
        raise MeshError(f"Cloudflare: {errs}")
    return j


async def create_node(name: str) -> tuple[str, str]:
    """(node_id, token)"""
    if not enabled():
        raise MeshError("CLOUDFLARE_API_TOKEN fehlt – Mesh-Knoten kann Health nicht anlegen")
    async with _client() as c:
        acc = await _account(c)
        j = _ok(await c.post(f"/accounts/{acc}/warp_connector", json={"name": name[:60]}))
        node = j["result"]["id"]
        t = _ok(await c.get(f"/accounts/{acc}/warp_connector/{node}/token"))
        token = t["result"] if isinstance(t["result"], str) else (t["result"] or {}).get("token", "")
        if not token:
            raise MeshError("Cloudflare hat keinen Token geliefert")
        return node, token


async def delete_node(node: str) -> None:
    if not enabled() or not node:
        return
    async with _client() as c:
        acc = await _account(c)
        r = await c.delete(f"/accounts/{acc}/warp_connector/{node}")
        if r.status_code not in (200, 404):
            _ok(r)
