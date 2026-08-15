from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import case, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.deps import (
    client_ip_dep,
    redis_dep,
    session_dep,
    settings_dep,
    user_session_dep,
)
from llm_gateway.api.registry import (
    _get_visible_mcp_or_404,
    _get_visible_skill_or_404,
)
from llm_gateway.core.config import Settings
from llm_gateway.db.models import (
    GatewayKey,
    MCP,
    McpTeamGrant,
    McpVersion,
    Project,
    ProjectMembership,
    RequestFact,
    RequestOutcome,
    ResourceState,
    Skill,
    SkillTeamGrant,
    SkillVersion,
    Subject,
    Team,
    TeamMembership,
    utcnow,
)
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.managed_memberships import (
    ManagedRole,
    managed_role_options,
    project_membership_payload,
    team_membership_payload,
)
from llm_gateway.services.policy import (
    list_accessible_model_aliases_for_subject,
    list_subject_team_memberships,
    list_subject_team_names,
)
from llm_gateway.services.registry import (
    SLUG_PATTERN,
    create_or_append_mcp_version,
    create_or_append_skill_version,
    ensure_mcp_team_grant,
    ensure_skill_team_grant,
    get_latest_active_mcp_version,
    get_latest_active_version,
    get_mcp_by_owner_slug,
    get_mcp_version_row,
    get_skill_by_owner_slug,
    get_skill_version,
    increment_skill_download_count,
    is_mcp_liked_by,
    is_skill_liked_by,
    list_visible_mcps,
    list_visible_skills,
    toggle_mcp_like,
    toggle_skill_like,
)
from llm_gateway.services.resource_payloads import (
    mcp_detail,
    mcp_summary,
    redact_gateway_key,
    skill_detail,
    skill_summary,
)
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
    session: AsyncSession = Depends(session_dep),
):
    start, end = _normalize_usage_window(start, end)
    if start and end and (end - start).days > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="time_window_exceeds_90_days",
        )
    if start is None and end is None:
        end = utcnow()
        start = end - timedelta(days=30)
    row = await _usage_summary_from_postgres(
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
    start, end = _normalize_usage_window(start, end)
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


@router.get("/managed/usage/ranking")
async def managed_usage_ranking(
    scope: str,
    resource_id: UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
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

    # 权限校验在 Python 端先做，传入 SQL 时已是安全值，不依赖 SQL 层过滤正确性。
    # team 排行只含当前 ACTIVE 成员（_team_subject_ids 已过滤
    # TeamMembership.state == ACTIVE），与 managed_usage_summary 的 team 分支一致。
    if scope == "project":
        await _require_project_manager(session, context.subject.id, resource_id)
        ranking = await _usage_ranking_from_postgres(
            session,
            start=start,
            end=end,
            project_ids=[resource_id],
            model=model,
            limit=limit,
        )
    elif scope == "team":
        await _require_team_manager(session, context.subject.id, resource_id)
        subject_ids = await _team_subject_ids(session, [resource_id])
        ranking = await _usage_ranking_from_postgres(
            session,
            start=start,
            end=end,
            subject_ids=subject_ids,
            model=model,
            limit=limit,
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
        "ranking": ranking,
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


class OwnKeyStatePatch(BaseModel):
    state: ResourceState


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
    personal_project = await _personal_project(session, context.subject)
    if (
        key is None
        or key.subject_id != context.subject.id
        or key.project_id != personal_project.id
    ):
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
        "team_memberships": await list_subject_team_memberships(
            session, subject_id=subject.id
        ),
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


def _normalize_usage_window(
    start: datetime | None, end: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Browsers send offset-aware datetimes (datetime-local converted with the
    browser's offset); RequestFact timestamps are naive UTC. Normalize before
    comparing so a Shanghai "15:00" filters on 07:00 UTC, not 15:00 UTC."""
    from llm_gateway.services.analytics import normalize_naive_utc

    return normalize_naive_utc(start), normalize_naive_utc(end)


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
    start, end = _normalize_usage_window(start, end)

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


async def _usage_ranking_from_postgres(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    project_ids: list[UUID] | None = None,
    subject_ids: list[UUID] | None = None,
    model: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Per-subject usage ranking, sorted by total_tokens desc.

    Mirrors _usage_summary_from_postgres (same total_tokens coalesce expression,
    same Postgres aggregation) but groups by subject and orders by usage. Used by
    the manager-facing ranking endpoint; the manager permission check happens in
    the route handler before this runs. subject_id IS NULL rows are excluded to
    match the admin ranking behavior.

    Scope is selected by passing exactly one of ``project_ids`` (filter on
    RequestFact.project_id) or ``subject_ids`` (filter on
    RequestFact.subject_id, used for team scope where membership is derived via
    TeamMembership). Empty lists short-circuit to [] like the summary builder.
    """
    start, end = _normalize_usage_window(start, end)
    if project_ids is not None and not project_ids:
        return []
    if subject_ids is not None and not subject_ids:
        return []

    total_tokens_expr = func.coalesce(
        RequestFact.total_tokens,
        func.coalesce(RequestFact.prompt_tokens, 0)
        + func.coalesce(RequestFact.completion_tokens, 0),
        0,
    )
    stmt = (
        select(
            Subject.id.label("subject_id"),
            Subject.name.label("subject_name"),
            Subject.login_username.label("login_username"),
            func.count(col(RequestFact.id)).label("request_count"),
            func.coalesce(func.sum(RequestFact.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(RequestFact.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(total_tokens_expr), 0).label("total_tokens"),
            func.coalesce(
                func.sum(
                    case((col(RequestFact.outcome) == RequestOutcome.SUCCESS, 1), else_=0)
                ),
                0,
            ).label("success_count"),
            func.coalesce(
                func.sum(
                    case((col(RequestFact.outcome) != RequestOutcome.SUCCESS, 1), else_=0)
                ),
                0,
            ).label("failure_count"),
        )
        .select_from(RequestFact)
        .outerjoin(Subject, RequestFact.subject_id == Subject.id)
        .where(col(RequestFact.subject_id).isnot(None))
    )
    if project_ids is not None:
        stmt = stmt.where(col(RequestFact.project_id).in_(project_ids))
    if subject_ids is not None:
        stmt = stmt.where(col(RequestFact.subject_id).in_(subject_ids))
    # Conditionally apply time bounds so a half-specified window (only start or
    # only end) behaves like _usage_summary_from_postgres rather than silently
    # returning [] because `started_at < NULL` is always false.
    if start is not None:
        stmt = stmt.where(col(RequestFact.started_at) >= start)
    if end is not None:
        stmt = stmt.where(col(RequestFact.started_at) < end)
    if model is not None:
        stmt = stmt.where(col(RequestFact.model_alias) == model)
    stmt = stmt.group_by(
        Subject.id, Subject.name, Subject.login_username
    ).order_by(
        desc(text("total_tokens")), desc(text("request_count"))
    ).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "subject_id": str(row.subject_id),
            "subject_name": row.subject_name or "无用户",
            "login_username": row.login_username,
            "request_count": int(row.request_count),
            "prompt_tokens": int(row.prompt_tokens),
            "completion_tokens": int(row.completion_tokens),
            "total_tokens": int(row.total_tokens),
            "success_count": int(row.success_count),
            "failure_count": int(row.failure_count),
        }
        for row in rows
    ]


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


# ---- marketplace: self-service skill registry ----

class SkillGrantCreate(BaseModel):
    team_id: UUID


@router.post("/registry/skills")
async def upload_skill(
    slug: str = Form(...),
    name: str = Form(...),
    version: str = Form(...),
    summary: str | None = Form(default=None),
    description: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    file: UploadFile = File(...),
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
    settings=Depends(settings_dep),
):
    import re

    if not re.match(SLUG_PATTERN, slug):
        raise HTTPException(status_code=422, detail="invalid_slug")
    zip_bytes = await file.read()
    if len(zip_bytes) > settings.marketplace_skill_max_bytes:
        raise HTTPException(status_code=413, detail="skill_too_large")
    if not zip_bytes:
        raise HTTPException(status_code=400, detail="empty_upload")
    skill = await create_or_append_skill_version(
        session,
        actor=ctx.subject,
        slug=slug,
        name=name,
        version=version,
        summary=summary,
        description=description,
        notes=notes,
        zip_bytes=zip_bytes,
    )
    await session.commit()
    await session.refresh(skill)
    return {"skill": skill_summary(skill, owner_name=ctx.subject.name)}


@router.get("/registry/skills")
async def list_my_skills(
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    from sqlalchemy import select as _select
    from sqlmodel import col as _col

    stmt = (
        _select(Skill)
        .where(_col(Skill.owner_subject_id) == ctx.subject.id)
        .order_by(_col(Skill.updated_at).desc())
    )
    items = list((await session.execute(stmt)).scalars().all())
    return {
        "items": [skill_summary(s, owner_name=ctx.subject.name) for s in items],
        "total": len(items),
    }


# ---- marketplace: browse (visible-to-me discovery) ----

@router.get("/registry/skills/browse")
async def browse_skills(
    q: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int | None = Query(default=None),
    sort: str = Query(default="downloads"),
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
):
    page_size = size or settings.marketplace_list_default_size
    page_size = min(page_size, settings.marketplace_list_max_size)
    offset = (page - 1) * page_size
    items, total = await list_visible_skills(
        session,
        subject_id=ctx.subject.id,
        q=q,
        owner=owner,
        limit=page_size,
        offset=offset,
        sort=sort,
    )
    owner_ids = {s.owner_subject_id for s in items}
    owner_names: dict[UUID, str] = {}
    if owner_ids:
        rows = await session.execute(
            select(Subject.id, Subject.name).where(col(Subject.id).in_(owner_ids))
        )
        owner_names = {row[0]: row[1] for row in rows.all()}
    return {
        "items": [
            skill_summary(s, owner_names.get(s.owner_subject_id)) for s in items
        ],
        "total": total,
        "page": page,
        "size": page_size,
    }


@router.get("/registry/skills/browse/{owner}/{slug}")
async def browse_skill_detail(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    versions = list(
        (
            await session.execute(
                select(SkillVersion)
                .where(
                    col(SkillVersion.skill_id) == skill.id,
                    col(SkillVersion.state) == ResourceState.ACTIVE,
                )
                .order_by(col(SkillVersion.created_at).desc())
            )
        ).scalars().all()
    )
    grants = list(
        (
            await session.execute(
                select(SkillTeamGrant).where(col(SkillTeamGrant.skill_id) == skill.id)
            )
        ).scalars().all()
    )
    owner_obj = await session.get(Subject, skill.owner_subject_id)
    liked_by_me = await is_skill_liked_by(
        session, subject_id=ctx.subject.id, skill_id=skill.id
    )
    return skill_detail(
        skill, versions, grants,
        owner_name=owner_obj.name if owner_obj else None,
        readme=skill.readme, liked_by_me=liked_by_me,
    )


@router.get("/registry/skills/browse/{owner}/{slug}/download")
async def browse_skill_download(
    owner: str,
    slug: str,
    version: str = Query(default="latest"),
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    if version == "latest":
        sv = await get_latest_active_version(session, skill=skill)
    else:
        sv = await get_skill_version(session, skill_id=skill.id, version=version)
    if sv is None:
        raise HTTPException(status_code=404, detail="version_not_found")
    await increment_skill_download_count(session, skill_id=skill.id)
    await session.commit()
    import io as _io

    return StreamingResponse(
        _io.BytesIO(sv.content_blob),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}-{sv.version}.zip"',
            "X-Content-SHA256": sv.content_sha256,
            "ETag": f'"{sv.content_sha256}"',
        },
    )


@router.post("/registry/skills/browse/{owner}/{slug}/like")
async def browse_skill_like(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    skill = await toggle_skill_like(
        session, subject_id=ctx.subject.id, skill_id=skill.id
    )
    await session.commit()
    return {"liked_by_me": True, "like_count": skill.like_count}


@router.delete("/registry/skills/browse/{owner}/{slug}/like")
async def browse_skill_unlike(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    skill = await toggle_skill_like(
        session, subject_id=ctx.subject.id, skill_id=skill.id
    )
    await session.commit()
    return {"liked_by_me": False, "like_count": skill.like_count}


async def _require_owned_skill(session, ctx, slug, include_disabled=False):
    skill = await get_skill_by_owner_slug(
        session, owner_id=ctx.subject.id, slug=slug, include_disabled=include_disabled
    )
    if skill is None or skill.owner_subject_id != ctx.subject.id:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    return skill


@router.get("/registry/skills/me/{slug}/grants")
async def list_my_skill_grants(
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _require_owned_skill(session, ctx, slug)
    from sqlalchemy import select as _select
    from sqlmodel import col as _col

    rows = (
        await session.execute(
            _select(SkillTeamGrant).where(_col(SkillTeamGrant.skill_id) == skill.id)
        )
    ).scalars().all()
    items = [
        {
            "id": str(g.id),
            "skill_id": str(g.skill_id),
            "team_id": str(g.team_id),
            "state": g.state.value if hasattr(g.state, "value") else g.state,
        }
        for g in rows
    ]
    return {"items": items, "total": len(items)}


@router.post("/registry/skills/me/{slug}/grants")
async def create_my_skill_grant(
    slug: str,
    payload: SkillGrantCreate,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _require_owned_skill(session, ctx, slug)
    team = await session.get(Team, payload.team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="team_not_found")
    grant = await ensure_skill_team_grant(
        session, skill_id=skill.id, team_id=payload.team_id
    )
    await session.commit()
    await session.refresh(grant)
    return {
        "grant": {
            "id": str(grant.id),
            "skill_id": str(grant.skill_id),
            "team_id": str(grant.team_id),
            "state": grant.state.value if hasattr(grant.state, "value") else grant.state,
        }
    }


@router.patch("/registry/skills/me/{slug}/grants/{grant_id}/state")
async def patch_my_skill_grant_state(
    slug: str,
    grant_id: UUID,
    payload: dict,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _require_owned_skill(session, ctx, slug)
    grant = await session.get(SkillTeamGrant, grant_id)
    if grant is None or grant.skill_id != skill.id:
        raise HTTPException(status_code=404, detail="grant_not_found")
    new_state = payload.get("state")
    if new_state not in ("active", "disabled"):
        raise HTTPException(status_code=422, detail="invalid_state")
    grant.state = ResourceState(new_state)
    grant.updated_at = utcnow()
    await session.commit()
    await session.refresh(grant)
    return {
        "grant": {
            "id": str(grant.id),
            "skill_id": str(grant.skill_id),
            "team_id": str(grant.team_id),
            "state": grant.state.value if hasattr(grant.state, "value") else grant.state,
        }
    }


# ---- marketplace: self-service MCP registry ----

class McpGrantCreate(BaseModel):
    team_id: UUID


@router.post("/registry/mcps")
async def publish_mcp(
    payload: dict,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    slug = payload.get("slug")
    name = payload.get("name")
    version = payload.get("version")
    if not slug or not name or not version:
        raise HTTPException(status_code=422, detail="missing_required_field")
    import re

    if not re.match(SLUG_PATTERN, slug):
        raise HTTPException(status_code=422, detail="invalid_slug")
    mcp = await create_or_append_mcp_version(
        session,
        actor=ctx.subject,
        slug=slug,
        name=name,
        version=version,
        summary=payload.get("summary"),
        description=payload.get("description"),
        notes=payload.get("notes"),
        config=payload.get("config") or {},
        readme=payload.get("readme"),
    )
    await session.commit()
    await session.refresh(mcp)
    return {"mcp": mcp_summary(mcp, owner_name=ctx.subject.name)}


@router.get("/registry/mcps")
async def list_my_mcps(
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    from sqlalchemy import select as _select
    from sqlmodel import col as _col

    stmt = (
        _select(MCP)
        .where(_col(MCP.owner_subject_id) == ctx.subject.id)
        .order_by(_col(MCP.updated_at).desc())
    )
    items = list((await session.execute(stmt)).scalars().all())
    return {
        "items": [mcp_summary(m, owner_name=ctx.subject.name) for m in items],
        "total": len(items),
    }


# ---- marketplace: browse (visible-to-me discovery) ----

@router.get("/registry/mcps/browse")
async def browse_mcps(
    q: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int | None = Query(default=None),
    sort: str = Query(default="downloads"),
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
):
    page_size = size or settings.marketplace_list_default_size
    page_size = min(page_size, settings.marketplace_list_max_size)
    offset = (page - 1) * page_size
    items, total = await list_visible_mcps(
        session,
        subject_id=ctx.subject.id,
        q=q,
        owner=owner,
        limit=page_size,
        offset=offset,
        sort=sort,
    )
    owner_ids = {m.owner_subject_id for m in items}
    owner_names: dict[UUID, str] = {}
    if owner_ids:
        rows = await session.execute(
            select(Subject.id, Subject.name).where(col(Subject.id).in_(owner_ids))
        )
        owner_names = {row[0]: row[1] for row in rows.all()}
    return {
        "items": [mcp_summary(m, owner_names.get(m.owner_subject_id)) for m in items],
        "total": total,
        "page": page,
        "size": page_size,
    }


@router.get("/registry/mcps/browse/{owner}/{slug}")
async def browse_mcp_detail(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _get_visible_mcp_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    versions = list(
        (
            await session.execute(
                select(McpVersion)
                .where(
                    col(McpVersion.mcp_id) == mcp.id,
                    col(McpVersion.state) == ResourceState.ACTIVE,
                )
                .order_by(col(McpVersion.created_at).desc())
            )
        ).scalars().all()
    )
    grants = list(
        (
            await session.execute(
                select(McpTeamGrant).where(col(McpTeamGrant.mcp_id) == mcp.id)
            )
        ).scalars().all()
    )
    latest = await get_latest_active_mcp_version(session, mcp=mcp)
    owner_obj = await session.get(Subject, mcp.owner_subject_id)
    reveal = mcp.owner_subject_id == ctx.subject.id
    liked_by_me = await is_mcp_liked_by(
        session, subject_id=ctx.subject.id, mcp_id=mcp.id
    )
    return mcp_detail(
        mcp, versions, latest, grants,
        owner_name=owner_obj.name if owner_obj else None,
        reveal=reveal, liked_by_me=liked_by_me, readme=mcp.readme,
    )


@router.post("/registry/mcps/browse/{owner}/{slug}/like")
async def browse_mcp_like(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _get_visible_mcp_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    mcp = await toggle_mcp_like(
        session, subject_id=ctx.subject.id, mcp_id=mcp.id
    )
    await session.commit()
    return {"liked_by_me": True, "like_count": mcp.like_count}


@router.delete("/registry/mcps/browse/{owner}/{slug}/like")
async def browse_mcp_unlike(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _get_visible_mcp_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    mcp = await toggle_mcp_like(
        session, subject_id=ctx.subject.id, mcp_id=mcp.id
    )
    await session.commit()
    return {"liked_by_me": False, "like_count": mcp.like_count}


async def _require_owned_mcp(session, ctx, slug, include_disabled=False):
    mcp = await get_mcp_by_owner_slug(
        session, owner_id=ctx.subject.id, slug=slug, include_disabled=include_disabled
    )
    if mcp is None or mcp.owner_subject_id != ctx.subject.id:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    return mcp


@router.get("/registry/mcps/me/{slug}/grants")
async def list_my_mcp_grants(
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _require_owned_mcp(session, ctx, slug)
    from sqlalchemy import select as _select
    from sqlmodel import col as _col

    rows = (
        await session.execute(
            _select(McpTeamGrant).where(_col(McpTeamGrant.mcp_id) == mcp.id)
        )
    ).scalars().all()
    items = [
        {
            "id": str(g.id),
            "mcp_id": str(g.mcp_id),
            "team_id": str(g.team_id),
            "state": g.state.value if hasattr(g.state, "value") else g.state,
        }
        for g in rows
    ]
    return {"items": items, "total": len(items)}


@router.post("/registry/mcps/me/{slug}/grants")
async def create_my_mcp_grant(
    slug: str,
    payload: McpGrantCreate,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _require_owned_mcp(session, ctx, slug)
    team = await session.get(Team, payload.team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="team_not_found")
    grant = await ensure_mcp_team_grant(
        session, mcp_id=mcp.id, team_id=payload.team_id
    )
    await session.commit()
    await session.refresh(grant)
    return {
        "grant": {
            "id": str(grant.id),
            "mcp_id": str(grant.mcp_id),
            "team_id": str(grant.team_id),
            "state": grant.state.value if hasattr(grant.state, "value") else grant.state,
        }
    }


@router.patch("/registry/mcps/me/{slug}/grants/{grant_id}/state")
async def patch_my_mcp_grant_state(
    slug: str,
    grant_id: UUID,
    payload: dict,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _require_owned_mcp(session, ctx, slug)
    grant = await session.get(McpTeamGrant, grant_id)
    if grant is None or grant.mcp_id != mcp.id:
        raise HTTPException(status_code=404, detail="grant_not_found")
    new_state = payload.get("state")
    if new_state not in ("active", "disabled"):
        raise HTTPException(status_code=422, detail="invalid_state")
    grant.state = ResourceState(new_state)
    grant.updated_at = utcnow()
    await session.commit()
    await session.refresh(grant)
    return {
        "grant": {
            "id": str(grant.id),
            "mcp_id": str(grant.mcp_id),
            "team_id": str(grant.team_id),
            "state": grant.state.value if hasattr(grant.state, "value") else grant.state,
        }
    }
