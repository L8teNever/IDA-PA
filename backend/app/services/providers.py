"""Which accounts can run which model: Claude models need a Claude account, "agy:…" models an Antigravity account."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Account
from . import antigravity
from .claude import models as claude_catalog
from .claude import usage
from .errors import Invalid

PROVIDERS = {"claude": "Claude", "antigravity": "Antigravity"}


def of_model(model: str | None) -> str:
    return "antigravity" if antigravity.is_model(model) else "claude"


def claude_models() -> list[str]:
    return claude_catalog.ids()


def known_model(model: str) -> bool:
    return model in get_settings().model_list or model in claude_catalog.ids() or antigravity.is_model(model)


def check_model(model: str) -> None:
    if not known_model(model):
        raise Invalid(f"Unbekanntes Modell „{model}“ – erlaubt: {', '.join(claude_catalog.ids())} oder agy:<modell>")


def logged_in(account: Account) -> bool:
    return antigravity.logged_in(account) if account.provider == "antigravity" else usage.logged_in(account)


async def all_accounts(db: AsyncSession) -> list[Account]:
    return list((await db.scalars(select(Account).order_by(Account.position, Account.id))).all())


async def model_list(db: AsyncSession) -> list[str]:
    """Every model the accounts can use: all Claude models (Anthropic's list) + the models of the Antigravity accounts."""
    accs = await all_accounts(db)
    claude_catalog.refresh_in_background(accs)
    return claude_catalog.ids() + antigravity.models(accs)


async def model_names(db: AsyncSession) -> dict[str, str]:
    accs = await all_accounts(db)
    return {**claude_catalog.names(), **antigravity.model_names(accs)}


async def exhausted(account: Account) -> bool:
    """At the limit (cached numbers) – then the next account of the same provider takes over."""
    try:
        return await (antigravity.exhausted(account) if account.provider == "antigravity" else usage.exhausted(account))
    except Exception:  # noqa: BLE001
        return False


async def accounts_for(db: AsyncSession, model: str | None) -> list[Account]:
    want = of_model(model)
    return [a for a in await all_accounts(db) if a.provider == want]


async def pick(db: AsyncSession, preferred_id: str | None, model: str | None) -> Account:
    """The preferred account if it can run the model, otherwise the first account of the right provider."""
    accs = await accounts_for(db, model)
    if not accs:
        name = PROVIDERS[of_model(model)]
        raise Invalid(f"Kein {name}-Account eingerichtet (Einstellungen → Accounts) – nötig für das Modell {model}")
    return next((a for a in accs if a.id == preferred_id), accs[0])
