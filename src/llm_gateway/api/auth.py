from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.deps import session_dep, settings_dep, user_session_dep
from llm_gateway.core.config import Settings
from llm_gateway.db.models import (
    GatewayKey,
    Project,
    RequestFact,
    RequestOutcome,
    ResourceState,
    Subject,
    utcnow,
)
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.policy import (
    list_accessible_model_aliases_for_subject,
    list_subject_team_names,
)
from llm_gateway.services.security import (
    UserSessionContext,
    create_gateway_key,
    create_registered_user,
    create_user_session,
    hash_password,
    normalize_username,
    revoke_user_session,
    verify_password,
)


router = APIRouter(prefix="/auth")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=9, max_length=9, pattern=r"^[A-Za-z]\d{8}$")
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class KeyIssueRequest(BaseModel):
    name: str = Field(default="personal-key", min_length=1, max_length=120)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class ProfileUpdateRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)


@router.post("/register")
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
):
    try:
        subject, project, key, raw_key = await create_registered_user(
            session,
            username=payload.username,
            full_name=payload.full_name,
            password=payload.password,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = (
            status.HTTP_409_CONFLICT
            if detail == "username_already_registered"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc
    user_session, raw_session = await create_user_session(
        session,
        subject_id=subject.id,
        ttl_hours=settings.session_ttl_hours,
    )
    await record_audit_event(
        session,
        actor_subject_id=subject.id,
        action="auth.register",
        resource_type="subject",
        resource_id=subject.id,
        outcome="success",
    )
    await session.commit()
    return {
        "session_token": raw_session,
        "session_expires_at": user_session.expires_at,
        "gateway_key": {"key": _redact_gateway_key(key), "plaintext_key": raw_key},
        "profile": await _profile_payload(session, subject),
        "project": project,
    }


@router.post("/login")
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
):
    username = normalize_username(payload.username)
    result = await session.execute(
        select(Subject).where(col(Subject.login_username) == username)
    )
    subject = result.scalar_one_or_none()
    if (
        not subject
        or not subject.password_hash
        or subject.state != ResourceState.ACTIVE
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_login"
        )
    if not verify_password(payload.password, subject.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_login"
        )
    user_session, raw_session = await create_user_session(
        session,
        subject_id=subject.id,
        ttl_hours=settings.session_ttl_hours,
    )
    await record_audit_event(
        session,
        actor_subject_id=subject.id,
        action="auth.login",
        resource_type="subject",
        resource_id=subject.id,
        outcome="success",
    )
    await session.commit()
    return {
        "session_token": raw_session,
        "session_expires_at": user_session.expires_at,
        "profile": await _profile_payload(session, subject),
    }


@router.post("/logout")
async def logout(
    request: Request,
    session: AsyncSession = Depends(session_dep),
):
    raw_token = request.headers.get("x-session-token")
    if not raw_token:
        auth = request.headers.get("authorization", "")
        raw_token = (
            auth[7:].strip() if auth.lower().startswith("bearer sess-") else None
        )
    if raw_token:
        await revoke_user_session(session, raw_token)
        await session.commit()
    return {"ok": True}


@router.get("/me")
async def me(
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    return await _profile_payload(session, context.subject)


@router.get("/usage/summary")
async def own_usage_summary(
    start: datetime | None = None,
    end: datetime | None = None,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    filters = [col(RequestFact.subject_id) == context.subject.id]
    if start:
        filters.append(col(RequestFact.started_at) >= start)
    if end:
        filters.append(col(RequestFact.started_at) < end)

    effective_total_tokens = func.coalesce(
        col(RequestFact.total_tokens),
        func.coalesce(col(RequestFact.prompt_tokens), 0)
        + func.coalesce(col(RequestFact.completion_tokens), 0),
        0,
    )
    success_count = func.coalesce(
        func.sum(
            case((col(RequestFact.outcome) == RequestOutcome.SUCCESS, 1), else_=0)
        ),
        0,
    ).label("success_count")
    failure_count = func.coalesce(
        func.sum(
            case((col(RequestFact.outcome) != RequestOutcome.SUCCESS, 1), else_=0)
        ),
        0,
    ).label("failure_count")

    stmt = select(
        func.count(col(RequestFact.id)).label("request_count"),
        func.coalesce(func.sum(col(RequestFact.prompt_tokens)), 0).label(
            "prompt_tokens"
        ),
        func.coalesce(func.sum(col(RequestFact.completion_tokens)), 0).label(
            "completion_tokens"
        ),
        func.coalesce(func.sum(effective_total_tokens), 0).label("total_tokens"),
        success_count,
        failure_count,
    ).where(*filters)
    row = (await session.execute(stmt)).mappings().one()
    return {
        "start": start,
        "end": end,
        "request_count": row["request_count"],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "total_tokens": row["total_tokens"],
        "success_count": row["success_count"],
        "failure_count": row["failure_count"],
    }


@router.patch("/password")
async def change_password(
    payload: PasswordChangeRequest,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    if not context.subject.password_hash or not verify_password(
        payload.current_password, context.subject.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="invalid_current_password"
        )
    context.subject.password_hash = hash_password(payload.new_password)
    context.subject.updated_at = utcnow()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="auth.password.change",
        resource_type="subject",
        resource_id=context.subject.id,
        outcome="success",
    )
    await session.commit()
    return {"ok": True}


@router.patch("/profile")
async def update_profile(
    payload: ProfileUpdateRequest,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    full_name = payload.full_name.strip()
    if not full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="full_name_required"
        )
    context.subject.name = full_name
    context.subject.updated_at = utcnow()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="auth.profile.update",
        resource_type="subject",
        resource_id=context.subject.id,
        outcome="success",
        detail={"field": "name"},
    )
    await session.commit()
    return await _profile_payload(session, context.subject)


@router.post("/keys")
async def issue_own_key(
    payload: KeyIssueRequest,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    project = await _personal_project(session, context.subject)
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
    return {"key": _redact_gateway_key(key), "plaintext_key": raw_key}


async def _profile_payload(session: AsyncSession, subject: Subject) -> dict[str, Any]:
    keys = (
        (
            await session.execute(
                select(GatewayKey)
                .where(col(GatewayKey.subject_id) == subject.id)
                .order_by(col(GatewayKey.created_at).desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "subject": _public_subject(subject),
        "teams": await list_subject_team_names(session, subject_id=subject.id),
        "models": await list_accessible_model_aliases_for_subject(
            session, subject_id=subject.id
        ),
        "keys": [_redact_gateway_key(key) for key in keys],
    }


async def _personal_project(session: AsyncSession, subject: Subject) -> Project:
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


def _public_subject(subject: Subject) -> dict[str, Any]:
    return {
        "id": subject.id,
        "name": subject.name,
        "type": subject.type,
        "state": subject.state,
        "notes": subject.notes,
        "login_username": subject.login_username,
        "is_admin": subject.is_admin,
        "requires_real_name": _requires_real_name(subject),
        "created_at": subject.created_at,
        "updated_at": subject.updated_at,
    }


def _requires_real_name(subject: Subject) -> bool:
    if subject.is_admin:
        return False
    name = subject.name.strip()
    username = normalize_username(subject.login_username or "")
    return not name or (bool(username) and normalize_username(name) == username)


def _redact_gateway_key(key: GatewayKey) -> dict[str, Any]:
    data = key.model_dump()
    data["key_hash"] = None
    return data
