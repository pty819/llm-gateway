from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.deps import (
    client_ip_dep,
    redis_dep,
    session_dep,
    settings_dep,
    user_session_dep,
)
from llm_gateway.core.config import Settings
from llm_gateway.db.models import (
    GatewayKey,
    Project,
    ProjectMembership,
    RequestFact,
    RequestOutcome,
    ResourceState,
    Subject,
    Team,
    TeamMembership,
    utcnow,
)
from llm_gateway.services.duckdb_analytics import get_analytics
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.managed_memberships import (
    ManagedRole,
    managed_role_options,
    project_membership_payload,
    team_membership_payload,
)
from llm_gateway.services.policy import (
    list_accessible_model_aliases_for_subject,
    list_subject_team_names,
)
from llm_gateway.services.resource_payloads import redact_gateway_key
from llm_gateway.services.rate_limit import RateLimitExceeded, check_login_rate
from llm_gateway.services.security import (
    DUMMY_PASSWORD_HASH,
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


class ManagedMembershipCreate(BaseModel):
    resource_id: UUID
    subject_id: UUID
    role: ManagedRole = ManagedRole.MEMBER


class ManagedTeamMembershipPatch(BaseModel):
    state: ResourceState


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
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
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
        "profile": await _profile_payload(session, subject),
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
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc

    result = await session.execute(
        select(Subject).where(col(Subject.login_username) == username)
    )
    subject = result.scalar_one_or_none()
    user_eligible = (
        subject is not None
        and subject.state == ResourceState.ACTIVE
        and bool(subject.password_hash)
    )
    # Always run a full PBKDF2 verification so the response timing cannot reveal
    # whether the username exists: unknown users verify against a dummy hash.
    stored_hash = subject.password_hash if (subject and subject.password_hash) else DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, stored_hash)
    if not user_eligible or not password_ok:
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
):
    if start and end and (end - start).days > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="time_window_exceeds_90_days",
        )
    if start is None and end is None:
        end = utcnow()
        start = end - timedelta(days=30)
    row = await get_analytics().own_usage_summary(
        subject_id=context.subject.id,
        start=start,
        end=end,
    )
    return {
        "start": start,
        "end": end,
        **row,
    }


@router.get("/managed/subjects")
async def list_managed_candidate_subjects(
    q: str | None = None,
    limit: int = 20,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await _require_any_managed_resource(session, context.subject.id)
    stmt = select(Subject).where(col(Subject.state) == ResourceState.ACTIVE)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            col(Subject.name).ilike(needle) | col(Subject.login_username).ilike(needle)
        )
    rows = (
        (
            await session.execute(
                stmt.order_by(col(Subject.name)).limit(max(1, min(limit, 50)))
            )
        )
        .scalars()
        .all()
    )
    return [_public_subject(subject) for subject in rows]


@router.get("/managed/roles")
async def list_managed_roles(
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await _require_any_managed_resource(session, context.subject.id)
    return managed_role_options()


@router.get("/managed/projects")
async def list_managed_projects(
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    return await _managed_projects_payload(session, context.subject.id)


@router.get("/managed/teams")
async def list_managed_teams(
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    return await _managed_teams_payload(session, context.subject.id)


@router.get("/managed/project-memberships")
async def list_managed_project_memberships(
    resource_id: UUID,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await _require_project_manager(session, context.subject.id, resource_id)
    rows = (
        await session.execute(
            select(ProjectMembership, Subject)
            .join(Subject, col(Subject.id) == col(ProjectMembership.subject_id))
            .where(col(ProjectMembership.project_id) == resource_id)
            .order_by(col(ProjectMembership.created_at).desc())
        )
    ).all()
    return [
        project_membership_payload(membership, subject) for membership, subject in rows
    ]


@router.get("/managed/team-memberships")
async def list_managed_team_memberships(
    resource_id: UUID,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await _require_team_manager(session, context.subject.id, resource_id)
    rows = (
        await session.execute(
            select(TeamMembership, Subject)
            .join(Subject, col(Subject.id) == col(TeamMembership.subject_id))
            .where(col(TeamMembership.team_id) == resource_id)
            .order_by(col(TeamMembership.created_at).desc())
        )
    ).all()
    return [
        team_membership_payload(membership, subject) for membership, subject in rows
    ]


@router.get("/managed/usage/summary")
async def managed_usage_summary(
    scope: str,
    resource_id: UUID | None = None,
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

    if scope == "project":
        project_ids = await _managed_project_ids(session, context.subject.id)
        if resource_id is not None:
            if resource_id not in project_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="not_project_manager",
                )
            project_ids = [resource_id]
        row = await _usage_summary_from_postgres(
            session,
            start=start,
            end=end,
            project_ids=project_ids,
        )
    elif scope == "team":
        team_ids = await _managed_team_ids(session, context.subject.id)
        if resource_id is not None:
            if resource_id not in team_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="not_team_manager",
                )
            team_ids = [resource_id]
        subject_ids = await _team_subject_ids(session, team_ids)
        row = await _usage_summary_from_postgres(
            session,
            start=start,
            end=end,
            subject_ids=subject_ids,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_managed_usage_scope",
        )
    return {
        "start": start,
        "end": end,
        "scope": scope,
        "resource_id": resource_id,
        **row,
    }


@router.post("/managed/project-memberships")
async def add_managed_project_member(
    payload: ManagedMembershipCreate,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await _require_project_manager(session, context.subject.id, payload.resource_id)
    subject = await _get_active_subject(session, payload.subject_id)
    result = await session.execute(
        select(ProjectMembership).where(
            col(ProjectMembership.project_id) == payload.resource_id,
            col(ProjectMembership.subject_id) == payload.subject_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        membership.role = payload.role.value
        membership.updated_at = utcnow()
    else:
        membership = ProjectMembership(
            project_id=payload.resource_id,
            subject_id=payload.subject_id,
            role=payload.role.value,
        )
        session.add(membership)
        await session.flush()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="managed.project_membership.upsert",
        resource_type="project_membership",
        resource_id=membership.id,
        outcome="success",
        detail={
            "project_id": str(payload.resource_id),
            "subject_id": str(payload.subject_id),
        },
    )
    await session.commit()
    await session.refresh(membership)
    return project_membership_payload(membership, subject)


@router.delete("/managed/project-memberships/{membership_id}")
async def remove_managed_project_member(
    membership_id: UUID,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    membership = await session.get(ProjectMembership, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await _require_project_manager(session, context.subject.id, membership.project_id)
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="managed.project_membership.delete",
        resource_type="project_membership",
        resource_id=membership.id,
        outcome="success",
        detail={
            "project_id": str(membership.project_id),
            "subject_id": str(membership.subject_id),
        },
    )
    await session.delete(membership)
    await session.commit()
    return {"ok": True}


@router.post("/managed/team-memberships")
async def add_managed_team_member(
    payload: ManagedMembershipCreate,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await _require_team_manager(session, context.subject.id, payload.resource_id)
    subject = await _get_active_subject(session, payload.subject_id)
    result = await session.execute(
        select(TeamMembership).where(
            col(TeamMembership.team_id) == payload.resource_id,
            col(TeamMembership.subject_id) == payload.subject_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        membership.role = payload.role.value
        membership.state = ResourceState.ACTIVE
        membership.updated_at = utcnow()
    else:
        membership = TeamMembership(
            team_id=payload.resource_id,
            subject_id=payload.subject_id,
            role=payload.role.value,
        )
        session.add(membership)
        await session.flush()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="managed.team_membership.upsert",
        resource_type="team_membership",
        resource_id=membership.id,
        outcome="success",
        detail={
            "team_id": str(payload.resource_id),
            "subject_id": str(payload.subject_id),
        },
    )
    await session.commit()
    await session.refresh(membership)
    return team_membership_payload(membership, subject)


@router.patch("/managed/team-memberships/{membership_id}")
async def set_managed_team_member_state(
    membership_id: UUID,
    payload: ManagedTeamMembershipPatch,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    membership = await session.get(TeamMembership, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await _require_team_manager(session, context.subject.id, membership.team_id)
    membership.state = payload.state
    membership.updated_at = utcnow()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="managed.team_membership.set_state",
        resource_type="team_membership",
        resource_id=membership.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    return membership


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
    return {"key": redact_gateway_key(key), "plaintext_key": raw_key}


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
        "keys": [redact_gateway_key(key) for key in keys],
        "managed": {
            "projects": await _managed_projects_payload(session, subject.id),
            "teams": await _managed_teams_payload(session, subject.id),
        },
    }


async def _managed_projects_payload(
    session: AsyncSession, subject_id: UUID
) -> list[dict[str, Any]]:
    stmt = (
        select(Project, ProjectMembership)
        .join(ProjectMembership, col(ProjectMembership.project_id) == col(Project.id))
        .where(
            col(Project.state) == ResourceState.ACTIVE,
            col(ProjectMembership.subject_id) == subject_id,
            col(ProjectMembership.role) == "manager",
        )
        .order_by(col(Project.name))
    )
    rows = (await session.execute(stmt)).all()
    return [
        {"project": project, "membership": membership} for project, membership in rows
    ]


async def _managed_teams_payload(
    session: AsyncSession, subject_id: UUID
) -> list[dict[str, Any]]:
    stmt = (
        select(Team, TeamMembership)
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == subject_id,
            col(TeamMembership.role) == "manager",
        )
        .order_by(col(Team.name))
    )
    rows = (await session.execute(stmt)).all()
    return [{"team": team, "membership": membership} for team, membership in rows]


async def _managed_project_ids(session: AsyncSession, subject_id: UUID) -> list[UUID]:
    rows = await _managed_projects_payload(session, subject_id)
    return [row["project"].id for row in rows]


async def _managed_team_ids(session: AsyncSession, subject_id: UUID) -> list[UUID]:
    rows = await _managed_teams_payload(session, subject_id)
    return [row["team"].id for row in rows]


async def _require_any_managed_resource(
    session: AsyncSession, subject_id: UUID
) -> None:
    if await _managed_project_ids(session, subject_id):
        return
    if await _managed_team_ids(session, subject_id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="not_resource_manager",
    )


async def _require_project_manager(
    session: AsyncSession, subject_id: UUID, project_id: UUID
) -> None:
    project_ids = await _managed_project_ids(session, subject_id)
    if project_id not in project_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not_project_manager",
        )


async def _require_team_manager(
    session: AsyncSession, subject_id: UUID, team_id: UUID
) -> None:
    team_ids = await _managed_team_ids(session, subject_id)
    if team_id not in team_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not_team_manager",
        )


async def _get_active_subject(session: AsyncSession, subject_id: UUID) -> Subject:
    subject = await session.get(Subject, subject_id)
    if not subject or subject.state != ResourceState.ACTIVE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return subject


async def _team_subject_ids(session: AsyncSession, team_ids: list[UUID]) -> list[UUID]:
    if not team_ids:
        return []
    result = await session.execute(
        select(col(TeamMembership.subject_id)).where(
            col(TeamMembership.team_id).in_(team_ids),
            col(TeamMembership.state) == ResourceState.ACTIVE,
        )
    )
    return list(result.scalars().all())


async def _usage_summary_from_postgres(
    session: AsyncSession,
    *,
    start: datetime | None,
    end: datetime | None,
    project_ids: list[UUID] | None = None,
    subject_ids: list[UUID] | None = None,
) -> dict[str, int]:
    if project_ids is not None and not project_ids:
        return _empty_usage_summary()
    if subject_ids is not None and not subject_ids:
        return _empty_usage_summary()

    total_tokens_expr = func.coalesce(
        RequestFact.total_tokens,
        func.coalesce(RequestFact.prompt_tokens, 0)
        + func.coalesce(RequestFact.completion_tokens, 0),
        0,
    )
    stmt = select(
        func.count(col(RequestFact.id)),
        func.coalesce(func.sum(RequestFact.prompt_tokens), 0),
        func.coalesce(func.sum(RequestFact.completion_tokens), 0),
        func.coalesce(func.sum(total_tokens_expr), 0),
        func.coalesce(
            func.sum(
                case((col(RequestFact.outcome) == RequestOutcome.SUCCESS, 1), else_=0)
            ),
            0,
        ),
        func.coalesce(
            func.sum(
                case((col(RequestFact.outcome) != RequestOutcome.SUCCESS, 1), else_=0)
            ),
            0,
        ),
    )
    if start is not None:
        stmt = stmt.where(col(RequestFact.started_at) >= start)
    if end is not None:
        stmt = stmt.where(col(RequestFact.started_at) < end)
    if project_ids is not None:
        stmt = stmt.where(col(RequestFact.project_id).in_(project_ids))
    if subject_ids is not None:
        stmt = stmt.where(col(RequestFact.subject_id).in_(subject_ids))

    row = (await session.execute(stmt)).one()
    return {
        "request_count": int(row[0] or 0),
        "prompt_tokens": int(row[1] or 0),
        "completion_tokens": int(row[2] or 0),
        "total_tokens": int(row[3] or 0),
        "success_count": int(row[4] or 0),
        "failure_count": int(row[5] or 0),
    }


def _empty_usage_summary() -> dict[str, int]:
    return {
        "request_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "success_count": 0,
        "failure_count": 0,
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
