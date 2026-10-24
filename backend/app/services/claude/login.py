"""Browser login for a Claude account, driven through the real `claude auth login`.

The container has no browser, so the CLI prints the sign-in URL and waits for the code ("Paste code here").
We start the CLI, hand the URL to the user, and later write the code they copied from claude.ai into its
stdin. The CLI itself stores the login (.credentials.json, incl. refresh token) in the account's config dir,
which lives on the /data volume – exactly as on a normal machine.
"""
import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass, field

from ...models import Account
from ..errors import Invalid
from .runner import _command, account_env
from .usage import logged_in

log = logging.getLogger("ida.login")
URL_RE = re.compile(r"https://\S*oauth/authorize\S+")
TIMEOUT = 600  # a started login is kept open for 10 minutes


@dataclass
class _Pending:
    proc: asyncio.subprocess.Process
    url: str
    started: float = field(default_factory=time.time)
    output: list[str] = field(default_factory=list)


_pending: dict[str, _Pending] = {}


async def _kill(acc_id: str) -> None:
    p = _pending.pop(acc_id, None)
    if p and p.proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            p.proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(p.proc.wait(), 5)


async def start(account: Account) -> str:
    """Starts `claude auth login` for the account and returns the sign-in URL."""
    await _kill(account.id)
    env = {**account_env(account), "BROWSER": "/bin/true"}
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)  # a setup-token must not shadow the new login
    proc = await asyncio.create_subprocess_exec(
        *_command(["auth", "login", "--claudeai"]), env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out: list[str] = []
    url = None
    try:
        async with asyncio.timeout(30):
            assert proc.stdout
            while not url:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                out.append(chunk.decode(errors="replace"))
                m = URL_RE.search("".join(out))
                if m:
                    url = m.group(0)
    except TimeoutError:
        pass
    if not url:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise Invalid("Claude hat keinen Anmelde-Link geliefert: " + "".join(out)[-300:])
    _pending[account.id] = _Pending(proc=proc, url=url, output=out)
    asyncio.get_running_loop().call_later(TIMEOUT, lambda: asyncio.ensure_future(_expire(account.id, proc)))
    return url


async def _expire(acc_id: str, proc) -> None:
    p = _pending.get(acc_id)
    if p and p.proc is proc:
        await _kill(acc_id)


async def finish(account: Account, code: str) -> dict:
    """Passes the code from claude.ai to the waiting CLI; returns whether the account is logged in now."""
    p = _pending.get(account.id)
    if not p or p.proc.returncode is not None:
        raise Invalid("Kein offener Login – bitte „Im Browser anmelden“ neu starten")
    code = code.strip()
    if not code or len(code) > 500 or any(c.isspace() for c in code):
        raise Invalid("Bitte den kompletten Code aus dem Browser einfügen")
    assert p.proc.stdin and p.proc.stdout
    p.proc.stdin.write((code + "\n").encode())
    await p.proc.stdin.drain()
    try:
        async with asyncio.timeout(60):
            rest = await p.proc.stdout.read()
            p.output.append(rest.decode(errors="replace"))
            await p.proc.wait()
    except TimeoutError:
        pass
    output = "".join(p.output)
    await _kill(account.id)
    if logged_in(account):
        account.oauth_token = None  # the real login replaces a pasted setup-token
        return {"ok": True}
    msg = output.split("Paste code here if prompted >")[-1].strip()[-300:] or "Anmeldung fehlgeschlagen"
    raise Invalid(f"Anmeldung fehlgeschlagen: {msg} – bitte „Im Browser anmelden“ neu starten")


async def cancel(account: Account) -> None:
    await _kill(account.id)
