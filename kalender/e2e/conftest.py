"""Browser-Tests (Playwright, Chromium im Handy-Format) gegen den ECHTEN Kalender-Dienst mit seiner Oberfläche.

Der Dienst läuft als eigener Prozess (damit ein Update-Test ihn mit geänderter Oberfläche neu starten kann); die Oberfläche liegt
in einer Kopie in einem Temp-Ordner. Test-Hook: `localStorage.kalTest = "1"` macht `window.__kal` sichtbar.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
KAL = ROOT / "kalender"
TOKEN = "e2e-ida-token-1234567890"


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """Ein Kalender-Dienst als Prozess; `restart()` startet ihn neu (gleiche Daten, gleiche Adresse)."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.front = tmp / "frontend"
        shutil.copytree(ROOT / "frontend", self.front, ignore=shutil.ignore_patterns("node_modules", "test"))
        self.data = tmp / "data"
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self.log = open(tmp / "server.log", "wb")

    def start(self) -> "Server":
        env = {**os.environ, "DATA_DIR": str(self.data), "FRONTEND_DIR": str(self.front), "SCHEDULER_ENABLED": "false", "KALENDER_TESTING": "1",
               "KALENDER_IDA_TOKEN": TOKEN, "TIMEZONE": "Europe/Berlin", "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port), "--log-level", "warning"],
                                     cwd=KAL, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(150):
            try:
                if httpx.get(f"{self.url}/api/v1/health", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError("Kalender-Dienst startet nicht:\n" + (self.tmp / "server.log").read_text(errors="replace")[-2000:])

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def restart(self) -> "Server":
        self.stop()
        return self.start()

    def edit(self, rel: str, append: str) -> None:
        """Eine Datei der Oberfläche ändern → nach dem Neustart gibt es eine neue App-Version."""
        p = self.front / "kalender" / rel
        p.write_text(p.read_text(encoding="utf8") + append, encoding="utf8")

    @property
    def api(self) -> httpx.Client:
        """Wie Ida (oder ein anderer Agent) – für Änderungen „von außen“."""
        return httpx.Client(base_url=self.url, headers={"Authorization": f"Bearer {TOKEN}", "X-Kalender-By": "ida"}, timeout=20)

    @property
    def user(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=20)


@pytest.fixture
def server():
    tmp = Path(tempfile.mkdtemp(prefix="kalender-e2e-"))
    s = Server(tmp).start()
    yield s
    s.stop()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def ctx(browser, server):
    """Handy-Browser mit erlaubtem Service Worker und Test-Hook."""
    c = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True, locale="de-DE", timezone_id="Europe/Berlin",
                            service_workers="allow", base_url=server.url)
    c.add_init_script("localStorage.setItem('kalTest', '1')")
    yield c
    c.close()


@pytest.fixture
def page(ctx, request):
    p = ctx.new_page()
    p.set_default_timeout(15000)
    msgs = []
    p.on("pageerror", lambda e: msgs.append(f"pageerror: {e}"))
    yield p
    if request.session.testsfailed and hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        out = Path(os.environ.get("E2E_ARTIFACTS", tempfile.gettempdir())) / f"{request.node.name}.png"
        try:
            p.screenshot(path=str(out))
        except Exception:  # noqa: BLE001
            pass
    assert not msgs, msgs


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    out = yield
    setattr(item, "rep_" + call.when, out.get_result())


# ---------------- Hilfen ----------------
def until(page, expr: str, timeout: float = 20.0, what: str = ""):
    """Wartet, bis ein JavaScript-Ausdruck (auch ein Promise) wahr ist; gibt den Wert zurück. (wait_for_function wartet nicht auf Promises.)"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = page.evaluate(f"(async () => {{ try {{ return await ({expr}); }} catch (e) {{ return null; }} }})()")
        except Exception as e:  # noqa: BLE001 – Seite lädt gerade
            last = None
        if last:
            return last
        time.sleep(0.15)
    raise AssertionError(f"Zeitüberschreitung: {what or expr} (zuletzt: {last!r})")


def open_app(page, path="/", wait_mirror=True):
    """App laden, warten bis sie steht, der Service Worker die Seite steuert und der Spiegel geschrieben ist."""
    page.goto(path)
    page.wait_for_selector("#title")
    page.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller !== null", timeout=20000)
    if wait_mirror:
        until(page, "window.__kal.mirrored()")


def version(page) -> str:
    return page.evaluate("window.__kal.version")


def go_offline(ctx):
    ctx.set_offline(True)


def go_online(ctx):
    ctx.set_offline(False)
