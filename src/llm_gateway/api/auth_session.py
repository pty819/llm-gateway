"""Auth session routes: register / login / logout / me / usage / password / profile.

Sub-router has NO prefix — it is included by ``api/auth.py`` under the shared
``/auth`` prefix, so paths compose to ``/auth/register`` etc. unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api._auth_helpers import public_subject
from llm_gateway.api.deps import (
    client_ip_dep,
    redis_dep,
    session_dep,
    settings_dep,
    user_session_dep,
)
from llm_gateway.api.managed import managed_projects_payload, managed_teams_payload
from llm_gateway.core.config import Settings
from llm_gateway.db.models import GatewayKey, ResourceState, Subject, utcnow
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.policy import (
    list_accessible_model_aliases_for_subject,
    list_subject_team_memberships,
    list_subject_team_names,
)
from llm_gateway.services.rate_limit import RateLimitExceeded, check_login_rate
from llm_gateway.services.resource_payloads import redact_gateway_key
from llm_gateway.services.security import (
    DUMMY_PASSWORD_HASH,
    UserSessionContext,
    create_registered_user,
    create_user_session,
    hash_password,
    normalize_username,
    revoke_user_session,
    verify_password,
)
from llm_gateway.services.usage_queries import usage_summary_from_postgres

router = APIRouter()


class RegisterRequest(BaseModel):
    username: str = Field(min_length=9, max_length=9, pattern=r"^[A-Za-z]\d{8}$")
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class ProfileUpdateRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)


async def profile_payload(session: AsyncSession, subject: Subject) -> dict[str, Any]:
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
        "subject": public_subject(subject),
        "teams": await list_subject_team_names(session, subject_id=subject.id),
        "team_memberships": await list_subject_team_memberships(session, subject_id=subject.id),
        "models": await list_accessible_model_aliases_for_subject(session, subject_id=subject.id),
        "keys": [redact_gateway_key(key) for key in keys],
        "managed": {
            "projects": await managed_projects_payload(session, subject.id),
            "teams": await managed_teams_payload(session, subject.id),
        },
    }


@router.post("/register")
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
    redis: Redis = Depends(redis_dep),
    client_ip: str = Depends(client_ip_dep),
):
    try:
        await check_login_rate(redis, client_ip=client_ip)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
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
        "gateway_key": {"key": redact_gateway_key(key), "plaintext_key": raw_key},
        "profile": await profile_payload(session, subject),
        "project": project,
    }


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
    redis: Redis = Depends(redis_dep),
    client_ip: str = Depends(client_ip_dep),
):
    username = normalize_username(payload.username)
    try:
        await check_login_rate(redis, client_ip=client_ip)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc

    result = await session.execute(select(Subject).where(col(Subject.login_username) == username))
    subject = result.scalar_one_or_none()
    user_eligible = (
        subject is not None
        and subject.state == ResourceState.ACTIVE
        and bool(subject.password_hash)
    )
    # Always run a full PBKDF2 verification so the response timing cannot reveal
    # whether the username exists: unknown users verify against a dummy hash.
    stored_hash = (
        subject.password_hash if (subject and subject.password_hash) else DUMMY_PASSWORD_HASH
    )
    password_ok = verify_password(payload.password, stored_hash)
    if not user_eligible or not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_login")
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
        "profile": await profile_payload(session, subject),
    }


@router.post("/logout")
async def logout(
    request: Request,
    session: AsyncSession = Depends(session_dep),
):
    raw_token = request.headers.get("x-session-token")
    if not raw_token:
        auth = request.headers.get("authorization", "")
        raw_token = auth[7:].strip() if auth.lower().startswith("bearer sess-") else None
    if raw_token:
        await revoke_user_session(session, raw_token)
        await session.commit()
    return {"ok": True}


@router.get("/me")
async def me(
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    return await profile_payload(session, context.subject)


@router.get("/usage/summary")
async def own_usage_summary(
    start: datetime | None = None,
    end: datetime | None = None,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    if start and end and (end - start).days > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="time_window_exceeds_90_days",
        )
    if start is None and end is None:
        end = utcnow()
        start = end - timedelta(days=30)
    row = await usage_summary_from_postgres(
        session,
        start=start,
        end=end,
        subject_ids=[context.subject.id],
    )
    return {
        "start": start,
        "end": end,
        **row,
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="full_name_required")
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
    return await profile_payload(session, context.subject)
