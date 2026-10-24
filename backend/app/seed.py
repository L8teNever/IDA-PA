"""Start data for an empty database: one Claude account slot, the agents from the design and a few skills."""
from sqlalchemy import func, select

from .config import get_settings
from .db import session_scope
from .models import Account, Agent, AgentSkill, Skill

AGENTS = [
    {"id": "ida", "is_main": True, "name": "Ida", "description": "Persönliche Assistentin", "palette": "blau", "home": "timeline",
     "intro": "Ich behalte deinen Tag im Blick und koordiniere die anderen.",
     "role": "Du bist die persönliche Assistentin des Nutzers. Du beantwortest Fragen, erledigst Aufgaben mit deinen Tools und "
             "koordinierst die anderen Agenten.",
     "chips": [["today", "Was steht heute an?"], ["checklist", "Was ist offen?"]]},
    {"id": "stella", "name": "Stella", "description": "Telefon & Voice", "palette": "pink",
     "intro": "Ich ruf dich an, nehm Anrufe an und red live mit dir.",
     "role": "Du kümmerst dich um Anrufe: anrufen, Anrufe annehmen, Weckrufe.",
     "chips": [["call", "Ruf mich an"], ["history", "Letzte Anrufe"], ["alarm", "Weckruf morgen"]]},
    {"id": "lea", "name": "Lea", "description": "Mails & Skills", "palette": "mint",
     "intro": "Ich sortier deine Mails und kümmer mich um deine Skills.",
     "role": "Du kümmerst dich um Mails: sortieren, zusammenfassen, Wichtiges melden.",
     "chips": [["inbox", "Posteingang sortieren"], ["label", "Neue Mail-Regel"]]},
]

SKILLS = [
    {"id": "zusammenfassen", "name": "Zusammenfassen", "icon": "summarize", "description": "Text oder letzte Antwort kurz zusammenfassen",
     "body": "Fasse den Inhalt zusammen: den Text des Nutzers oder, wenn er keinen angibt, deine letzte Antwort. Erst ein Satz mit der "
             "Kernaussage, danach höchstens 5 Stichpunkte. Nichts hinzuerfinden."},
    {"id": "uebersetzen", "name": "Übersetzen", "icon": "translate", "description": "Ins Englische oder in die genannte Sprache übersetzen",
     "body": "Übersetze den Text ins Englische; nennt der Nutzer eine andere Zielsprache, übersetze dorthin. Gib nur die Übersetzung aus."},
    {"id": "einfach", "name": "Einfach erklären", "icon": "child_care", "description": "Verständlich und einfach erklären",
     "body": "Erkläre das Thema so einfach wie möglich: kurze Sätze, ein Alltagsbeispiel, keine Fachbegriffe ohne Erklärung."},
    {"id": "korrigieren", "name": "Korrigieren", "icon": "spellcheck", "description": "Rechtschreibung und Grammatik korrigieren",
     "body": "Korrigiere Rechtschreibung, Grammatik und Zeichensetzung, ohne Stil oder Inhalt zu ändern. Gib zuerst den korrigierten Text "
             "aus, darunter in einer Zeile, was du geändert hast."},
    {"id": "merken", "name": "Merken", "icon": "psychology", "description": "Etwas dauerhaft im Gedächtnis speichern",
     "body": "Speichere den genannten Inhalt dauerhaft im Gedächtnis (memory_save, passende Art und Wichtigkeit; gilt es für alle Agenten, "
             "im gemeinsamen Bereich). Bestätige in einem Satz, was gespeichert wurde."},
]


async def seed() -> None:
    s = get_settings()
    async with session_scope() as db:
        if not await db.scalar(select(func.count()).select_from(Account)):
            db.add(Account(id="a1", name="Account 1", position=0))
        if await db.scalar(select(func.count()).select_from(Agent)):
            return
        for i, a in enumerate(AGENTS):
            db.add(Agent(**{"model": s.default_model, "position": i, **a}))
        for k in SKILLS:
            db.add(Skill(**k))
        await db.flush()
        for k in SKILLS:
            for a in AGENTS:
                db.add(AgentSkill(agent_id=a["id"], skill_id=k["id"]))
