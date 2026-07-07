"""Self-service gateway-key routes: issue + set-state.

Sub-router has NO prefix — it is included by ``api/auth.py`` under the shared
``/auth`` prefix, so paths compose to ``/auth/keys`` etc. unchanged.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.deps import session_dep, user_session_dep
from llm_gateway.db.models import GatewayKey, Project, ResourceState, Subject, utcnow
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.resource_payloads import redact_gateway_key
from llm_gateway.services.security import UserSessionContext, create_gateway_key

router = APIRouter()


class KeyIssueRequest(BaseModel):
    name: str = Field(default="personal-key", min_length=1, max_length=120)


class OwnKeyStatePatch(BaseModel):
    state: ResourceState


async def personal_project(session: AsyncSession, subject: Subject) -> Project:
    result = await session.execute(
        select(Project)
        .where(col(Project.owner_subject_id) == subject.id)
        .order_by(col(Project.created_at))
    )
    project = result.scalars().first()
    if project:
        return project
    project = Project(
        name=f"user-{subject.login_username or subject.id}",
        owner_subject_id=subject.id,
        notes="Self-service personal project.",
    )
    session.add(project)
    await session.flush()
    return project


@router.post("/keys")
async def issue_own_key(
    payload: KeyIssueRequest,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    project = await personal_project(session, context.subject)
    key, raw_key = await create_gateway_key(
        session,
        subject_id=context.subject.id,
        project_id=project.id,
        name=payload.name,
    )
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="auth.key.issue",
        resource_type="gateway_key",
        resource_id=key.id,
        outcome="success",
        detail={"key_prefix": key.key_prefix},
    )
    await session.commit()
    return {"key": redact_gateway_key(key), "plaintext_key": raw_key}


@router.patch("/keys/{key_id}/state")
async def set_own_key_state(
    key_id: UUID,
    payload: OwnKeyStatePatch,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    # 权限双重校验：key 必须属于当前用户的个人 project。
    # issue_own_key 只往个人 project 发 key，这两条等价于"自己创建的 key"。
    # 别人的 key 或跨 project 的 key，对当前用户而言"不存在"——404 而非 403，
    # 避免向用户泄露其他 key 的存在性（最小信息泄露）。
    key = await session.get(GatewayKey, key_id)
    personal_proj = await personal_project(session, context.subject)
    if key is None or key.subject_id != context.subject.id or key.project_id != personal_proj.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key_not_found")
    key.state = payload.state
    key.updated_at = utcnow()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="auth.key.set_state",
        resource_type="gateway_key",
        resource_id=key.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    await session.refresh(key)
    return {"key": redact_gateway_key(key)}
