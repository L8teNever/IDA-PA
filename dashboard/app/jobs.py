"""Länger laufende Vorgänge (z. B. `docker compose pull`) im Hintergrund mit Live-Ausgabe.

Die Oberfläche startet einen Vorgang, bekommt eine ID und folgt der Ausgabe per NDJSON. Schließt man die Seite,
läuft der Vorgang trotzdem zu Ende.
"""
import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

KEEP = 50  # so viele fertige Vorgänge bleiben abrufbar


@dataclass
class Job:
    id: str
    title: str
    target: str
    lines: list[str] = field(default_factory=list)
    done: bool = False
    ok: bool = False
    started: float = field(default_factory=time.time)
    ended: float | None = None
    _wake: asyncio.Event = field(default_factory=asyncio.Event)

    def write(self, text: str) -> None:
        for line in text.splitlines() or [""]:
            self.lines.append(line)
        self._wake.set()

    def view(self) -> dict:
        return {"id": self.id, "title": self.title, "target": self.target, "done": self.done, "ok": self.ok,
                "started": self.started, "ended": self.ended, "lines": len(self.lines)}


class Jobs:
    def __init__(self):
        self.items: dict[str, Job] = {}
        self.tasks: set[asyncio.Task] = set()

    def busy(self, target: str) -> Job | None:
        return next((j for j in self.items.values() if j.target == target and not j.done), None)

    def start(self, title: str, target: str, work: Callable[[Job], Awaitable[bool]]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], title=title, target=target)
        self.items[job.id] = job

        async def run():
            try:
                job.ok = bool(await work(job))
            except Exception as e:  # noqa: BLE001 – der Fehler gehört in die Ausgabe, nicht in den Server-Log
                job.write(f"Fehler: {e}")
                job.ok = False
            job.done, job.ended = True, time.time()
            job._wake.set()
            self._trim()

        t = asyncio.create_task(run())
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return job

    def _trim(self) -> None:
        done = sorted((j for j in self.items.values() if j.done), key=lambda j: j.ended or 0)
        for j in done[:-KEEP]:
            self.items.pop(j.id, None)

    async def follow(self, job: Job) -> AsyncIterator[str]:
        sent = 0
        yield json.dumps({"job": job.view()}) + "\n"
        while True:
            job._wake.clear()
            while sent < len(job.lines):
                yield json.dumps({"line": job.lines[sent]}) + "\n"
                sent += 1
            if job.done:
                yield json.dumps({"done": job.view()}) + "\n"
                return
            try:
                await asyncio.wait_for(job._wake.wait(), 15)
            except TimeoutError:
                yield json.dumps({"ping": True}) + "\n"   # hält die Verbindung durch Proxys offen


async def run_cmd(job: Job, args: list[str], cwd: str | None = None, env: dict | None = None) -> bool:
    job.write("$ " + " ".join(args))
    proc = await asyncio.create_subprocess_exec(*args, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    assert proc.stdout
    buf = b""
    while chunk := await proc.stdout.read(4096):
        buf += chunk
        # Fortschrittsbalken arbeiten mit \r – jede fertige Zeile einzeln weitergeben
        *full, buf = buf.replace(b"\r", b"\n").split(b"\n")
        for line in full:
            if line.strip():
                job.write(line.decode("utf-8", "replace"))
    if buf.strip():
        job.write(buf.decode("utf-8", "replace"))
    code = await proc.wait()
    job.write(f"→ beendet mit Code {code}")
    return code == 0
