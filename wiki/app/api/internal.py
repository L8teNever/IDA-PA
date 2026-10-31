"""Interne Schnittstelle ausschließlich für IDA (`/api/v1/internal/*`, geschützt durch `WIKI_IDA_TOKEN`):
1. Vorbereiten, Bestätigen und Ablehnen von Freigabe-Proposals für `privat/`
2. Echtzeit-Spiegelung von IDA-Agenten, Chats, Code-Projekten und Code-Threads nach `ki/`.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import Caller, require_ida_internal
from ..db import session_scope
from ..services import chat_mirror, proposals

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


class ProposalCreateIn(BaseModel):
    agent_id: str = "main"
    action: str
    payload: dict


class ProposalCommitIn(BaseModel):
    commit_secret: str


@router.post("/proposals")
async def internal_create_proposal(body: ProposalCreateIn, c: Caller = Depends(require_ida_internal)):
    agent_id = body.agent_id or (c.by if c.kind == "ida" else "main")
    async with session_scope(write=True) as db:
        return await proposals.prepare_proposal(db, agent_id, body.action, body.payload)


@router.post("/proposals/{prop_id}/commit")
async def internal_commit_proposal(prop_id: str, body: ProposalCommitIn, _: Caller = Depends(require_ida_internal)):
    async with session_scope(write=True) as db:
        return await proposals.execute_proposal(db, prop_id, commit_secret=body.commit_secret)


@router.post("/proposals/{prop_id}/reject")
async def internal_reject_proposal(prop_id: str, _: Caller = Depends(require_ida_internal)):
    async with session_scope(write=True) as db:
        return await proposals.reject_proposal(db, prop_id)


@router.get("/sync/state")
async def internal_sync_state(_: Caller = Depends(require_ida_internal)):
    async with session_scope(write=False) as db:
        return await chat_mirror.sync_state(db)


@router.post("/sync/agent")
async def internal_sync_agent(payload: dict, _: Caller = Depends(require_ida_internal)):
    async with session_scope(write=True) as db:
        return await chat_mirror.sync_agent(db, payload)


@router.delete("/sync/agent/{agent_id}")
async def internal_delete_agent(agent_id: str, _: Caller = Depends(require_ida_internal)):
    async with session_scope(write=True) as db:
        return await chat_mirror.delete_agent(db, agent_id)


@router.post("/sync/project")
async def internal_sync_project(payload: dict, _: Caller = Depends(require_ida_internal)):
    async with session_scope(write=True) as db:
        return await chat_mirror.sync_project(db, payload)


@router.delete("/sync/project/{project_id}")
async def internal_delete_project(project_id: str, _: Caller = Depends(require_ida_internal)):
    async with session_scope(write=True) as db:
        return await chat_mirror.delete_project(db, project_id)


@router.post("/sync/chat")
async def internal_sync_chat(payload: dict, _: Caller = Depends(require_ida_internal)):
    async with session_scope(write=True) as db:
        return await chat_mirror.sync_chat(db, payload)


@router.delete("/sync/chat/{chat_id}")
async def internal_delete_chat(chat_id: str, _: Caller = Depends(require_ida_internal)):
    async with session_scope(write=True) as db:
        return await chat_mirror.delete_chat(db, chat_id)
