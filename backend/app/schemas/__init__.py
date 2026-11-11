"""Request bodies (pydantic). Responses are plain dicts built by the serializers in api/serializers.py.

Every "Update" schema has only optional fields; routers pass `model_dump(exclude_unset=True)` to the
services, so a PATCH changes exactly the fields that were sent.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    intro: str = ""
    role: str = ""
    personality: str = ""
    instructions: str = ""
    palette: str = "gelb"
    home: str = "simple"
    chips: list[list[str]] = Field(default_factory=list)
    model: str | None = None
    account_id: str | None = None
    connectors: list[str] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    intro: str | None = None
    role: str | None = None
    personality: str | None = None
    instructions: str | None = None
    palette: str | None = None
    home: str | None = None
    chips: list[list[str]] | None = None
    model: str | None = None
    account_id: str | None = None
    fallback: bool | None = None
    memory_enabled: bool | None = None
    enabled: bool | None = None


class Notes(BaseModel):
    text: str = ""


class Access(BaseModel):
    allowed: bool
    tools: list[str] | None = None  # None = all enabled tools of the connector


class ChatSend(BaseModel):
    chat_id: str | None = None
    agent_id: str | None = None
    text: str = ""
    model: str | None = None
    account_id: str | None = None
    skill: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None  # Claude: how much it thinks
    files: list[str] = Field(default_factory=list)  # ids from POST /uploads (pictures, PDFs, text files)
    project_id: str | None = None  # code area: start a new thread of this project
    permission_mode: Literal["ask", "edits", "full"] | None = None  # code threads: rights (ask = every edit/command needs an OK)


class ChatUpdate(BaseModel):
    title: str | None = None
    pinned: bool | None = None


class Trigger(BaseModel):
    type: str | None = None  # mcp (fixed check) | script (own code)
    connector: str = ""
    tool: str = ""
    code: str = ""  # type script: Python code, see services/trigger_script.py
    args: dict[str, Any] | str = Field(default_factory=dict)
    every_sec: int = 300
    mode: str = "new"
    contains: str = ""


class RoutineCreate(BaseModel):
    name: str
    prompt: str
    icon: str | None = None
    agent_id: str | None = None
    model: str | None = None
    account_id: str | None = None
    interval_min: int | None = None
    times: list[str] | None = None  # fixed times "HH:MM"
    days: list[int] | None = None  # 0 = Monday … 6 = Sunday, empty = every day
    run_at: str | None = None  # only to answer clearly: one-time things are reminders (see ReminderCreate)
    on_demand: bool | None = None  # no schedule: only started via MCP / "Jetzt ausführen"
    trigger: Trigger | None = None
    fallback: bool | None = None
    memory_enabled: bool | None = None
    show_on_home: bool | None = None


class RoutineUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    icon: str | None = None
    agent_id: str | None = None
    model: str | None = None
    account_id: str | None = None
    interval_min: int | None = None
    times: list[str] | None = None
    days: list[int] | None = None
    on_demand: bool | None = None
    trigger: Trigger | None = None
    fallback: bool | None = None
    memory_enabled: bool | None = None
    enabled: bool | None = None
    show_on_home: bool | None = None


class ReminderCreate(BaseModel):
    text: str
    when: str  # "2026-10-26T09:00" (local time)
    kind: str = "remind"  # remind = only a notification | task = the agent does it
    agent_id: str | None = None
    show_on_home: bool = True


class ReminderUpdate(BaseModel):
    text: str | None = None
    when: str | None = None
    kind: str | None = None
    agent_id: str | None = None
    show_on_home: bool | None = None


class TrackerSlot(BaseModel):
    key: str | None = None  # kept on changes, so ticks stay attached
    label: str = ""
    time: str | None = None  # "HH:MM" – followed up then if still open; None = only tick


class TrackerCreate(BaseModel):
    name: str
    icon: str | None = None
    note: str | None = None
    slots: list[TrackerSlot] = Field(default_factory=lambda: [TrackerSlot()])
    days: list[int] | None = None  # 0 = Monday … 6 = Sunday, empty = every day
    on_missed: str = "none"  # none | notify | routine
    routine_id: str | None = None
    show_on_home: bool = True
    duration_s: int = 0  # > 0: a timer runs before the tick (link / timer page)
    next_id: str | None = None  # after the tick the page goes straight on with this tracker


class TrackerUpdate(BaseModel):
    name: str | None = None
    icon: str | None = None
    note: str | None = None
    slots: list[TrackerSlot] | None = None
    days: list[int] | None = None
    on_missed: str | None = None
    routine_id: str | None = None
    show_on_home: bool | None = None
    enabled: bool | None = None
    duration_s: int | None = None
    next_id: str | None = None


class TrackerTick(BaseModel):
    slot: str | None = None  # key; empty = the first open one
    day: str | None = None  # "YYYY-MM-DD", empty = today
    undo: bool = False


class TrackerLinkCreate(BaseModel):
    name: str = ""
    slot: str | None = None  # key; None = automatically by the time of day


class HomeCalendar(BaseModel):
    off: bool = False  # no calendar on the start page
    source: str | None = None  # "kalender" = the local IDA calendar service (docs/KALENDER.md)
    connector: str | None = None  # empty = pick automatically
    tool: str | None = None
    args: dict[str, Any] | None = None  # None = fill the date arguments from the tool's schema


class TriggerTest(BaseModel):
    trigger: Trigger


class ConnectorOAuth(BaseModel):
    grant: Literal["code", "client_credentials"] = "code"  # code = Login im Browser, client_credentials = nur ID + Secret
    client_id: str | None = None      # leer bei code = automatisch registrieren (falls der Server das kann)
    client_secret: str | None = None  # None = bisheriges behalten (beim Ändern), "" = löschen
    scope: str | None = None


class ConnectorCreate(BaseModel):
    name: str = ""
    url: str
    token: str | None = None
    oauth: ConnectorOAuth | None = None
    agents: list[str] = Field(default_factory=list)


class ConnectorUpdate(BaseModel):
    name: str | None = None
    context: str | None = None
    enabled: bool | None = None
    disabled_tools: list[str] | None = None


class SkillCreate(BaseModel):
    id: str | None = None
    name: str
    icon: str | None = None
    description: str = ""
    body: str
    agents: list[str] = Field(default_factory=list)


class SkillUpdate(BaseModel):
    name: str | None = None
    icon: str | None = None
    description: str | None = None
    body: str | None = None
    enabled: bool | None = None
    agents: list[str] | None = None


class MemoryAdd(BaseModel):
    scope: str
    text: str
    type: str | None = None
    pinned: bool | None = None
    importance: int | None = None
    expires_in_days: int | None = None


class MemoryUpdate(BaseModel):
    scope: str
    text: str | None = None
    type: str | None = None
    pinned: bool | None = None
    importance: int | None = None
    expires_in_days: int | None = None


class Scope(BaseModel):
    scope: str


class AccountCreate(BaseModel):
    name: str = ""
    provider: str = "claude"  # claude | antigravity


class AccountUpdate(BaseModel):
    name: str | None = None
    oauth_token: str | None = None


class UserMessage(BaseModel):
    to: str
    text: str
    priority: str = "normal"


# ---- code area (projects, threads, git, GitHub) ----

Mode = Literal["ask", "edits", "full"]  # rights of a code thread


class CodeSettings(BaseModel):
    enabled: bool


class NewRepo(BaseModel):
    name: str
    private: bool = True
    description: str = ""


class ProjectCreate(BaseModel):
    name: str = ""
    repo_url: str | None = None  # clone this (https://…); with neither repo_url nor new_repo: empty local repo
    new_repo: NewRepo | None = None  # create a new GitHub repo and clone it
    server_stacks: list[str] = Field(default_factory=list, max_length=20)  # Projekt aus Docker-Stacks des Servers (ohne Repo)
    context: str = Field("", max_length=20_000)


class ProjectUpdate(BaseModel):
    name: str | None = None
    server_stack: str | None = None  # "" = keinen Stack mehr (alt: nur einer)
    server_stacks: list[str] | None = Field(None, max_length=20)  # [] = keine
    context: str | None = Field(None, max_length=20_000)


class Draft(BaseModel):
    text: str = Field("", max_length=200_000)
    files: list[str] = Field(default_factory=list, max_length=20)


class ChatDraftItem(BaseModel):
    """A waiting message of a normal chat (same fields as POST /chat)."""
    text: str = Field("", max_length=200_000)
    skill: str | None = None
    model: str | None = None
    account_id: str | None = None
    effort: str | None = None
    files: list[str] = Field(default_factory=list, max_length=20)


class ChatDraft(Draft):
    """What is unsent in a normal chat: the input field, its files and the waiting messages."""
    queue: list[ChatDraftItem] = Field(default_factory=list, max_length=50)


class ChatFork(BaseModel):
    message_id: str = Field(min_length=1, max_length=12)


class QueueItem(BaseModel):
    """A message that waits in a code thread's queue (same fields as POST /chat)."""
    text: str = Field("", max_length=200_000)
    model: str | None = None
    account_id: str | None = None
    effort: str | None = None
    skill: str | None = None
    files: list[str] = Field(default_factory=list, max_length=20)


class ThreadUpdate(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    permission_mode: Mode | None = None


class GitCommit(BaseModel):
    message: str
    push: bool = False


class CommitMessageSuggest(BaseModel):
    model: str | None = None
    file: str | None = None  # only this file's diff, else everything


class GitBranch(BaseModel):
    name: str
    create: bool = True


class PullRequest(BaseModel):
    title: str
    body: str = ""
    base: str | None = None  # default: the project's default branch


class Publish(BaseModel):
    name: str = ""
    private: bool = True


class SandboxRun(BaseModel):
    befehl: str = Field(min_length=1, max_length=20000)
    timeout_s: int = Field(default=120, ge=1, le=600)
    projekt_kopieren: bool = False


class ApprovalAnswer(BaseModel):
    decision: Literal["allow", "deny", "always"]


class GithubToken(BaseModel):
    token: str


class GithubClient(BaseModel):
    client_id: str


class AutoRoute(BaseModel):
    provider_mode: Literal["per_chat", "free", "claude", "antigravity"] = "per_chat"
    models: list[str] | None = None  # None = default pool (curated current models)
    profiles: dict[str, dict[str, int | None]] | None = None  # own strength/skills per model; None = keep the saved ones


class RouteRating(BaseModel):
    message_id: str
    rating: Literal["ok", "weak", "strong"] | None = None  # empty with better_model: follows from it
    better_model: str | None = None
