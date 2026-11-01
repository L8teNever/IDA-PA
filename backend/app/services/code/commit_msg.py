"""Short commit messages from the current diff – a separate cheap model call, no chat context."""
import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from .. import providers, runs
from ..claude import runner
from ..claude.runner import RunSpec
from ..errors import Invalid
from . import git

PROMPT = (
    "Schreibe eine kurze Git-Commit-Nachricht (eine Zeile, max. 72 Zeichen) auf Deutsch "
    "fuer den folgenden Diff. Nur die Nachricht selbst, ohne Anfuehrungszeichen und ohne Erklaerung.\n\n"
    "Diff:\n"
)


def clean(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text).strip()
    for line in text.splitlines():
        line = line.strip().strip("\"'`")
        if line:
            return line[:300]
    return text[:300]


async def suggest(db: AsyncSession, path: Path, *, model: str | None = None, file: str | None = None) -> dict:
    """Returns {message, model} from the project diff. Uses cheap_model unless model is set."""
    dinfo = await git.diff(path, file)
    if not dinfo["diff"].strip():
        raise Invalid("Keine Änderungen")
    model = (model or get_settings().cheap_model).strip()
    providers.check_model(model)
    acc = await runs.account_or_first(db, None, model)
    out = await runner.run(RunSpec(
        model=model, account=acc, prompt=PROMPT + dinfo["diff"][:20000],
        system="Du schreibst knappe, praezise Git-Commit-Nachrichten. Antworte nur mit der Nachricht.",
    ))
    if not out.ok:
        raise Invalid((out.text or "Vorschlag fehlgeschlagen")[:300])
    msg = clean(out.text)
    if not msg:
        raise Invalid("Leere Antwort vom Modell")
    return {"message": msg, "model": model}
