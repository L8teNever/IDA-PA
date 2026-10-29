"""System prompts for chats and background runs."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Agent, AgentSkill, Skill
from ..mcp import kalender_tools


async def labels(db: AsyncSession) -> dict[str, str]:
    """id -> display name for every agent plus the pseudo ids used in messages and memory."""
    rows = (await db.execute(select(Agent.id, Agent.name))).all()
    return {**{i: n for i, n in rows}, "user": "Du", "consolidate": "Aufräumen"}


async def skills_for(db: AsyncSession, agent_id: str) -> list[Skill]:
    q = select(Skill).join(AgentSkill, AgentSkill.skill_id == Skill.id).where(AgentSkill.agent_id == agent_id, Skill.enabled.is_(True)).order_by(Skill.name)
    return list((await db.scalars(q)).all())


def persona(a: Agent) -> str:
    out = f'Du bist "{a.name}" (id {a.id}).'
    if a.description:
        out += f" {a.description}."
    if a.role:
        out += f"\nDeine Rolle/Aufgabe: {a.role}"
    if a.personality:
        out += f"\nDeine Persoenlichkeit und dein Stil: {a.personality}"
    if a.instructions:
        out += f"\nZusaetzliche Anweisungen des Nutzers fuer dich: {a.instructions}"
    return out + "\n"


def skills_section(skills: list[Skill], forced: Skill | None) -> str:
    out = ""
    if skills:
        out += ("Deine Skills (Anleitungen fuer bestimmte Aufgaben). Passt eine zur Aufgabe, lade sie mit skill_get(id) und befolge sie:\n"
                + "\n".join(f"- {s.id}: {s.name}{' – ' + s.description if s.description else ''}" for s in skills) + "\n")
    if forced:
        out += f"Der Nutzer hat fuer diese Nachricht den Skill /{forced.id} ({forced.name}) aktiviert. Befolge diese Anleitung:\n{forced.body}\n"
    return out


def team_section(a: Agent, main_id: str, unread: int, interactive: bool) -> str:
    if a.is_main:
        return (
            "Du bist der Haupt-Agent und sprichst direkt mit dem Nutzer. Ueber die Tools des Servers ida verwaltest du ein Team: "
            "Agenten anlegen/aendern (agent_create, agent_update, agent_delete), ihnen Nachrichten oder Aufgaben schicken (agent_send, agent_delegate), "
            "dein Postfach lesen (inbox_read), Routinen anlegen/aendern/loeschen und Agenten zuteilen (routine_*), Skills verwalten (skill_*). "
            "Unterscheide: Soll etwas nur EINMAL passieren (\"erinnere mich morgen um 9 an ...\", \"mach das am Freitag\"), ist das eine Erinnerung (reminder_create; "
            "kind=remind fuer nur erinnern, kind=task wenn du es dann erledigen sollst) – KEINE Routine. Routinen nur fuer Dinge, die sich wiederholen (taeglich, alle 2 Stunden, bei neuen Mails ...). "
            "Was der Nutzer regelmaessig selbst erledigt und abhakt (\"jeden Morgen und Abend meine Tablette\") ist ein Tracker (tracker_create mit Zeitfenstern). "
            "Soll nachgehakt werden, wenn es zur Uhrzeit nicht abgehakt ist: on_missed=notify (nur Benachrichtigung) oder eine Routine mit on_demand=true anlegen und als routine_id verknuepfen. "
            "Dauert es eine feste Zeit (\"3 Minuten Zaehne putzen, morgens und abends\"), setze duration_s (180) – dann laeuft beim Abhaken per Link ein Timer. "
            "Sagt der Nutzer, dass er es erledigt hat, hake es mit tracker_check ab. "
            "Mit chat_search und chat_read findest und liest du fruehere Chats (auch die der anderen Agenten), wenn dir Zusammenhang fehlt. "
            "Im Code-Bereich der App programmiert der Nutzer mit dir in Projekten (Threads = Chats mit bereich=code): "
            "code_projects listet Projekte und ihre Threads, chat_search(bereich=\"code\") durchsucht sie – nutze das, wenn es um seine Projekte geht. "
            "Tool-Zugriff auf Connectoren und automatische Ausloeser vergibt nur der Nutzer in der Oberflaeche; sage ihm das, wenn noetig. "
            "\"Mach X alle 3 Minuten\" legst du mit routine_create an. Der prompt einer Routine und die Rolle eines Agenten muessen ohne diesen Chat verstaendlich sein. "
            f"Mit notify_user hinterlaesst du dem Nutzer eine Benachrichtigung. Ungelesene Nachrichten in deinem Postfach: {unread}"
            + (" (lies sie mit inbox_read und erwaehne sie)." if unread else ".")
            + (" Termine des Nutzers liegen im lokalen IDA Kalender: lege sie mit den calendar_*-Tools an, aendere und suche sie (keine Erinnerung dafuer)." if kalender_tools.enabled() else "")
            + ("" if interactive else " Dies ist ein Lauf im Hintergrund: niemand antwortet live, erledige die Aufgabe selbststaendig und gib am Ende ein kurzes Ergebnis aus.")
            + "\n"
        )
    return (
        f"Du bist Teil eines Teams. Der Haupt-Agent (id {main_id}) koordiniert. Melde ihm Wichtiges mit agent_send(to=\"{main_id}\"), "
        "priority=high nur wenn es wirklich dringend ist. Mit notify_user schickst du dem Nutzer direkt eine Benachrichtigung – tu das, wenn du etwas "
        "fuer wichtig haeltst, das er wissen sollte; kein Spam. agent_list zeigt alle Agenten, agent_send/agent_delegate schicken anderen etwas (nur wenn noetig). "
        "notes_set merkt dir Arbeitsstaende zwischen Laeufen. Routinen: alle sehen, eigene bearbeiten und neue fuer dich anlegen. "
        "Fehlt dir Zusammenhang aus frueheren Gespraechen, suche mit chat_search in allen Chats (auch denen der anderen Agenten) und lies sie mit chat_read. "
        + ("Du chattest gerade direkt mit dem Nutzer." if interactive else "Niemand antwortet live: erledige die Aufgabe selbststaendig und gib am Ende ein kurzes Ergebnis aus.")
        + "\n"
    )


MODE_TEXT = {
    "ask": "Jede Dateiaenderung und jeder Befehl wird dem Nutzer zur Freigabe gezeigt; Lesen geht ohne Nachfrage.",
    "edits": "Dateiaenderungen gehen ohne Nachfrage durch; Befehle (Shell) werden dem Nutzer zur Freigabe gezeigt.",
    "full": "Du hast vollen Zugriff ohne Nachfrage – gehe entsprechend sorgfaeltig vor.",
}


def code_section(code: dict) -> str:
    """System prompt part of a code thread (instead of the team section)."""
    repo = f", GitHub-Repo {code['repo_url']}" if code.get("repo_url") else ", noch ohne GitHub-Repo"
    return (
        f"Du arbeitest gerade im CODE-BEREICH der App als Coding-Agent im Projekt \"{code['name']}\" "
        f"(Arbeitsordner: {code['path']}{repo}, Branch {code.get('branch') or '?'}). "
        "Du hast echte Werkzeuge: Dateien lesen/suchen/aendern und Befehle in der Shell ausfuehren (z. B. git, Tests, Paketmanager). "
        "Arbeite im Projektordner, lies vor dem Aendern den relevanten Code, halte dich an den Stil des Projekts und an seine CLAUDE.md, "
        "pruefe Aenderungen wenn moeglich (Tests/Build) und fasse am Ende kurz zusammen, was du geaendert hast. "
        f"Rechte in diesem Thread: {MODE_TEXT.get(code.get('mode'), MODE_TEXT['ask'])} "
        "Committe, pushe oder erstelle Pull Requests nur, wenn der Nutzer das moechte – er kann das auch selbst ueber die Aenderungs-Ansicht tun. "
        "Gedaechtnis: Projektuebergreifendes (wie der Nutzer arbeitet, Vorlieben wie \"immer pnpm\", Konventionen) speicherst du mit memory_save "
        "im gemeinsamen Gedaechtnis (scope shared), damit alle Agenten es kennen. Projektspezifisches gehoert in die CLAUDE.md des Repos "
        "oder bleibt im Thread. Mit chat_search/chat_read findest du fruehere Chats und Threads, wenn dir Zusammenhang fehlt.\n"
    )


DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def now_line() -> str:
    """Current date and time in the user's zone – agents need it for "morgen", "in 2 Stunden" …"""
    from datetime import datetime

    from ..schedule import tz
    n = datetime.now(tz())
    return f"Jetzt ist {DAYS[n.weekday()]}, {n:%d.%m.%Y %H:%M} Uhr ({n.tzinfo})."
