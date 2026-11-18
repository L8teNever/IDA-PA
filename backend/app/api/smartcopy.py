"""Smart Copy der Chats-App: ein Text geht an das günstige Claude-Modell (Haiku, `cheap_model`), das daraus herausschreibt, was man einzeln kopieren will
(Bestätigungscodes, E-Mail-Adressen, Telefonnummern, Adressen …). Der Dienst "chats" fragt hier (intern), prüft die Antwort selbst und fällt bei Fehler
oder Limit auf seine einfache Erkennung zurück. Bei Limit probiert er – wie die Tippfehler-Korrektur – den nächsten Claude-Account."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import session_scope
from ..services import providers
from ..services.claude import runner
from ..services.claude.runner import RunSpec

router = APIRouter(prefix="/api/v1/smartcopy", tags=["smartcopy"])

SYSTEM = ("Du bekommst eine Chat-Nachricht. Schreibe ALLES heraus, was man daraus sinnvoll einzeln kopieren könnte – lieber eine Zeile zu viel als zu wenig: "
          "Bestätigungs-, Verifizierungs- und Anmeldecodes, PINs, TANs, Passwörter, WLAN-Namen, Benutzernamen, E-Mail-Adressen, Telefonnummern, Postadressen, Links, IBANs, "
          "Datumsangaben, Uhrzeiten, Geldbeträge, Bestell-, Rechnungs-, Kunden-, Sendungs-, Vorgangs- und Referenznummern, Kennzeichen und sonstige Nummern oder Kennungen. "
          "Kein normaler Fließtext, keine einzelnen gewöhnlichen Wörter. "
          "Jeder Wert muss WÖRTLICH in der Nachricht stehen: genau so abschreiben, nichts ergänzen, nichts umformatieren, nichts erfinden. "
          "Eine Adresse ist eine zusammenhängende Angabe (Straße, Hausnummer, PLZ, Ort), kein einzelnes Wort. "
          "Antworte NUR mit einem JSON-Array, ohne Erklärung und ohne Codeblock. Jedes Element: "
          '{"type": "code" | "email" | "phone" | "address" | "link" | "iban" | "date" | "time" | "amount" | "number" | "other", '
          '"label": "kurze deutsche Bezeichnung, z. B. Bestätigungscode von Epic Games, Rechnungsnummer, Abfahrt", "value": "der Wert"}. '
          "Steht nichts Kopierbares darin, antworte mit [].")


class Text(BaseModel):
    text: str = Field(max_length=4000)


@router.post("/haiku")
async def haiku(body: Text):
    """{ok, text, model, account} – `text` ist die rohe Antwort des Modells (JSON-Array); ok=false, wenn kein Account kann (dann nimmt chats die einfache Erkennung)."""
    text = body.text.strip()
    if not text:
        return {"ok": False}
    model = get_settings().cheap_model
    async with session_scope() as db:
        accs = [a for a in await providers.accounts_for(db, model) if providers.logged_in(a)]
    for acc in accs:
        if await providers.exhausted(acc):
            continue
        out = await runner.run(RunSpec(model=model, account=acc, prompt=f"<nachricht>\n{text}\n</nachricht>", system=SYSTEM, effort="low"))
        if out.ok and out.text.strip():
            return {"ok": True, "text": out.text.strip(), "model": model, "account": acc.name}
        if not out.limit_hit:
            break
    return {"ok": False}
