"""System prompts for chats and background runs."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Agent, AgentSkill, Skill


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
            "Tool-Zugriff auf Connectoren und automatische Ausloeser vergibt nur der Nutzer in der Oberflaeche; sage ihm das, wenn noetig. "
            "\"Mach X alle 3 Minuten\" legst du mit routine_create an. Der prompt einer Routine und die Rolle eines Agenten muessen ohne diesen Chat verstaendlich sein. "
            f"Mit notify_user hinterlaesst du dem Nutzer eine Benachrichtigung. Ungelesene Nachrichten in deinem Postfach: {unread}"
            + (" (lies sie mit inbox_read und erwaehne sie)." if unread else ".")
            + ("" if interactive else " Dies ist ein Lauf im Hintergrund: niemand antwortet live, erledige die Aufgabe selbststaendig und gib am Ende ein kurzes Ergebnis aus.")
            + "\n"
        )
    return (
        f"Du bist Teil eines Teams. Der Haupt-Agent (id {main_id}) koordiniert. Melde ihm Wichtiges mit agent_send(to=\"{main_id}\"), "
        "priority=high nur wenn es wirklich dringend ist. Mit notify_user schickst du dem Nutzer direkt eine Benachrichtigung – tu das, wenn du etwas "
        "fuer wichtig haeltst, das er wissen sollte; kein Spam. agent_list zeigt alle Agenten, agent_send/agent_delegate schicken anderen etwas (nur wenn noetig). "
        "notes_set merkt dir Arbeitsstaende zwischen Laeufen. Routinen: alle sehen, eigene bearbeiten und neue fuer dich anlegen. "
        + ("Du chattest gerade direkt mit dem Nutzer." if interactive else "Niemand antwortet live: erledige die Aufgabe selbststaendig und gib am Ende ein kurzes Ergebnis aus.")
        + "\n"
    )
