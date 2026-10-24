"""Request bodies (pydantic). Responses are plain dicts built by the serializers in api/serializers.py.

Every "Update" schema has only optional fields; routers pass `model_dump(exclude_unset=True)` to the
services, so a PATCH changes exactly the fields that were sent.
"""
from typing import Any

from pydantic import BaseModel, Field


class Login(BaseModel):
    password: str


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


class ChatUpdate(BaseModel):
    title: str


class Trigger(BaseModel):
    type: str = "mcp"
    connector: str
    tool: str
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
    trigger: Trigger | None = None
    fallback: bool | None = None
    memory_enabled: bool | None = None


class RoutineUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    icon: str | None = None
    agent_id: str | None = None
    model: str | None = None
    account_id: str | None = None
    interval_min: int | None = None
    trigger: Trigger | None = None
    fallback: bool | None = None
    memory_enabled: bool | None = None
    enabled: bool | None = None


class TriggerTest(BaseModel):
    trigger: Trigger


class ConnectorCreate(BaseModel):
    name: str = ""
    url: str
    token: str | None = None
    agents: list[str] = Field(default_factory=list)


class ConnectorUpdate(BaseModel):
    name: str | None = None
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


class AccountUpdate(BaseModel):
    name: str | None = None
    oauth_token: str | None = None


class LoginCode(BaseModel):
    code: str


class UserMessage(BaseModel):
    to: str
    text: str
    priority: str = "normal"
