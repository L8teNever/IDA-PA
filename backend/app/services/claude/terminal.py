"""A real terminal (PTY) running the Claude CLI for one account – like logging in with `claude auth login`
in T3 Code. The browser shows it with xterm.js over a WebSocket.

The process keeps running when the browser disconnects (switching to Claude on the phone closes the socket),
the last output is buffered and replayed when the terminal is opened again. Idle sessions end after 15 minutes.
Linux only (the container); on Windows development machines the endpoint reports that it is unavailable.
"""
import asyncio
import contextlib
import logging
import os
import signal
import struct
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ...config import get_settings
from ...models import Account
from ..errors import Invalid
from .runner import _command, account_env

log = logging.getLogger("ida.terminal")
COMMANDS = {
    "login": ["auth", "login", "--claudeai"],
    "status": ["auth", "status"],
    "logout": ["auth", "logout"],
}
# Antigravity accounts: plain `agy` asks for the Google sign-in (link + code), then opens its own chat –
# leave it with Ctrl+C. `agy models` shows whether the login works.
AGY_COMMANDS = {"login": [], "status": ["models"]}
BUFFER = 64 * 1024
IDLE_LIMIT = 15 * 60


@dataclass
class Session:
    account_id: str
    command: str
    proc: asyncio.subprocess.Process
    master: int
    buffer: bytearray = field(default_factory=bytearray)
    listeners: set[Callable[[bytes | None], None]] = field(default_factory=set)
    last_active: float = field(default_factory=time.time)
    exit_code: int | None = None

    @property
    def running(self) -> bool:
        return self.exit_code is None

    def feed(self, data: bytes | None) -> None:
        if data:
            self.buffer += data
            del self.buffer[:-BUFFER]
        for fn in list(self.listeners):
            fn(data)

    def write(self, data: bytes) -> None:
        self.last_active = time.time()
        if self.running:
            with contextlib.suppress(OSError):
                os.write(self.master, data)

    def resize(self, cols: int, rows: int) -> None:
        import fcntl
        import termios
        with contextlib.suppress(OSError):
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", max(rows, 5), max(cols, 20), 0, 0))

    def kill(self) -> None:
        if self.running:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.proc.pid, signal.SIGTERM)


_sessions: dict[str, Session] = {}


def available() -> bool:
    return sys.platform != "win32"


async def open_session(account: Account, command: str, cols: int = 100, rows: int = 30) -> Session:
    """Returns the running session of the account (same command) or starts a new one."""
    if not available():
        raise Invalid("Das Terminal gibt es nur im Docker-Container (Linux)")
    agy = account.provider == "antigravity"
    if command not in (AGY_COMMANDS if agy else COMMANDS):
        raise Invalid("Unbekannter Befehl")
    old = _sessions.get(account.id)
    if old and old.running and old.command == command:
        old.last_active = time.time()
        return old
    if old:
        old.kill()
    import pty

    master, slave = pty.openpty()
    if agy:
        from .. import antigravity
        antigravity.lock_down(account.id)
        env = {**antigravity.env(account), "TERM": "xterm-256color", "COLORTERM": "truecolor", "BROWSER": "/bin/true"}
        argv, work = antigravity.command(*AGY_COMMANDS[command]), antigravity.home(account.id)
    else:
        env = {**account_env(account), "TERM": "xterm-256color", "COLORTERM": "truecolor", "BROWSER": "/bin/true", "FORCE_COLOR": "1"}
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)  # a pasted setup-token must not shadow the real login
        argv, work = _command(COMMANDS[command]), get_settings().sandbox_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=work, start_new_session=True)
    os.close(slave)
    os.set_blocking(master, False)
    s = Session(account_id=account.id, command=command, proc=proc, master=master)
    s.resize(cols, rows)
    _sessions[account.id] = s
    loop = asyncio.get_running_loop()

    def readable() -> None:
        try:
            data = os.read(master, 4096)
        except BlockingIOError:
            return
        except OSError:  # EIO: the process closed the terminal
            data = b""
        if data:
            s.feed(data)
        else:
            loop.remove_reader(master)

    loop.add_reader(master, readable)

    async def wait_exit() -> None:
        s.exit_code = await proc.wait()
        await asyncio.sleep(0.2)  # let the last output arrive
        with contextlib.suppress(Exception):
            loop.remove_reader(master)
        with contextlib.suppress(OSError):
            os.close(master)
        s.feed(None)

    loop.create_task(wait_exit())
    return s


def get(account_id: str) -> Session | None:
    return _sessions.get(account_id)


async def reap() -> None:
    """Ends idle sessions and forgets finished ones (called by the scheduler)."""
    now = time.time()
    for acc_id, s in list(_sessions.items()):
        if now - s.last_active > IDLE_LIMIT:
            s.kill()
            if not s.running:
                _sessions.pop(acc_id, None)
