from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.admin._common import (
    StatePatch,
    _audit_update,
    _count_rows,
    _get_or_404,
)
from llm_gateway.api.deps import redis_dep, session_dep
from llm_gateway.db.models import (
    GatewayKey,
    ModelAlias,
    ModelEntitlement,
    Project,
    ResourceState,
    Subject,
    Team,
    TeamMembership,
    TeamTokenQuota,
    ModelTeamGrant,
    utcnow,
)
from llm_gateway.services.resource_payloads import apply_model_patch, paginated
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.security import ensure_model_team_grant, ensure_team_membership
from llm_gateway.services.team_quota import current_window


router = APIRouter()


class ModelEntitlementCreate(BaseModel):
    model_alias_id: UUID
    subject_id: UUID | None = None
    project_id: UUID | None = None
    gateway_key_id: UUID | None = None


class TeamCreate(BaseModel):
    name: str
    notes: str | None = None


class TeamUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None
    state: ResourceState | None = None


class TeamMembershipCreate(BaseModel):
    team_id: UUID
    subject_id: UUID
    role: str = "member"


class ModelTeamGrantCreate(BaseModel):
    model_alias_id: UUID
    team_id: UUID


class TeamTokenQuotaPayload(BaseModel):
    """Full-replace quota config; NULL = unlimited for that window."""

    morning_tokens: int | None = Field(default=None, ge=0, le=10_000_000_000)
    afternoon_tokens: int | None = Field(default=None, ge=0, le=10_000_000_000)
    evening_tokens: int | None = Field(default=None, ge=0, le=10_000_000_000)
    state: ResourceState | None = None


@router.post("/model-entitlements")
async def create_model_entitlement(
    payload: ModelEntitlementCreate, session: AsyncSession = Depends(session_dep)
):
    await _get_or_404(session, ModelAlias, payload.model_alias_id)
    scope_values = [payload.subject_id, payload.project_id, payload.gateway_key_id]
    if sum(value is not None for value in scope_values) != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="exactly_one_entitlement_scope_required",
        )
    if payload.subject_id:
        await _get_or_404(session, Subject, payload.subject_id)
    if payload.project_id:
        await _get_or_404(session, Project, payload.project_id)
    if payload.gateway_key_id:
        await _get_or_404(session, GatewayKey, payload.gateway_key_id)
    entitlement = ModelEntitlement(**payload.model_dump())
    session.add(entitlement)
    await session.flush()
    await record_audit_event(
        session,
        action="model_entitlement.create",
        resource_type="model_entitlement",
        resource_id=entitlement.id,
        outcome="success",
    )
    await session.commit()
    await session.refresh(entitlement)
    return entitlement


@router.get("/model-entitlements")
async def list_model_entitlements(
    model_alias_id: UUID | None = Query(default=None),
    subject_id: UUID | None = Query(default=None),
    project_id: UUID | None = Query(default=None),
    gateway_key_id: UUID | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    filters = []
    if model_alias_id:
        filters.append(col(ModelEntitlement.model_alias_id) == model_alias_id)
    if subject_id:
        filters.append(col(ModelEntitlement.subject_id) == subject_id)
    if project_id:
        filters.append(col(ModelEntitlement.project_id) == project_id)
    if gateway_key_id:
        filters.append(col(ModelEntitlement.gateway_key_id) == gateway_key_id)
    # Related names are embedded so a paginated client can render each row
    # without holding the full subject/project/key/model inventories.
    base = (
        select(
            ModelEntitlement,
            ModelAlias.alias,
            Subject.name,
            Subject.login_username,
            Project.name,
            GatewayKey.name,
        )
        .outerjoin(
            ModelAlias, col(ModelEntitlement.model_alias_id) == col(ModelAlias.id)
        )
        .outerjoin(Subject, col(ModelEntitlement.subject_id) == col(Subject.id))
        .outerjoin(Project, col(ModelEntitlement.project_id) == col(Project.id))
        .outerjoin(
            GatewayKey, col(ModelEntitlement.gateway_key_id) == col(GatewayKey.id)
        )
    )
    if filters:
        base = base.where(*filters)
    total = await _count_rows(
        session, select(func.count()).select_from(base.subquery())
    )
    rows = (
        await session.execute(
            base.order_by(col(ModelEntitlement.created_at).desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = []
    for entitlement, alias, subject_name, subject_login, project_name, key_name in rows:
        item = entitlement.model_dump()
        item["model_alias"] = alias
        item["subject_name"] = subject_name
        item["subject_login_username"] = subject_login
        item["project_name"] = project_name
        item["key_name"] = key_name
        items.append(item)
    return paginated(items, total, limit, offset)


@router.post("/teams")
async def create_team(
    payload: TeamCreate, session: AsyncSession = Depends(session_dep)
):
    team = Team(**payload.model_dump())
    session.add(team)
    await session.flush()
    await record_audit_event(
        session,
        action="team.create",
        resource_type="team",
        resource_id=team.id,
        outcome="success",
        detail={"name": team.name},
    )
    await session.commit()
    await session.refresh(team)
    return team


@router.get("/teams")
async def list_teams(
    q: str | None = Query(default=None, max_length=120),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    stmt = select(Team)
    if q and q.strip():
        stmt = stmt.where(col(Team.name).ilike(f"%{q.strip()}%"))
    total = await _count_rows(
        session, select(func.count()).select_from(stmt.subquery())
    )
    rows = (
        (
            await session.execute(
                stmt.order_by(col(Team.name)).offset(offset).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return paginated(rows, total, limit, offset)


@router.get("/teams/options")
async def list_team_options(
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=200, ge=1, le=2000),
    session: AsyncSession = Depends(session_dep),
):
    """Lightweight id/name pairs for searchable pickers (grant editors,
    marketplace authorization); the cap is generous so those flows don't
    regress on large team counts."""
    stmt = select(Team.id, Team.name).order_by(col(Team.name))
    if q and q.strip():
        stmt = stmt.where(col(Team.name).ilike(f"%{q.strip()}%"))
    rows = (await session.execute(stmt.limit(limit))).all()
    return [{"id": str(row.id), "name": row.name} for row in rows]


@router.patch("/teams/{team_id}")
async def update_team(
    team_id: UUID, payload: TeamUpdate, session: AsyncSession = Depends(session_dep)
):
    team = await _get_or_404(session, Team, team_id)
    apply_model_patch(team, payload)
    await _audit_update(session, "team.update", "team", team.id, payload)
    await session.commit()
    await session.refresh(team)
    return team


def _quota_row_payload(
    row: TeamTokenQuota, team_name: str | None = None
) -> dict:
    """Serialize a quota row. Limits apply per member (each member gets their
    own budget), so there is no team-level "used" number anymore — per-member
    usage is served by the member-usage endpoint for the teams drawer."""
    from llm_gateway.services.team_quota import current_window

    window, _window_date, _end = current_window()
    limit_by_window = {
        "morning": "morning_tokens",
        "afternoon": "afternoon_tokens",
        "evening": "evening_tokens",
    }
    return {
        "team_id": str(row.team_id),
        "team_name": team_name,
        "morning_tokens": row.morning_tokens,
        "afternoon_tokens": row.afternoon_tokens,
        "evening_tokens": row.evening_tokens,
        "state": row.state.value,
        "current_window": window,
        "current_window_limit": getattr(row, limit_by_window[window]),
        "current_window_used": None,
    }


@router.get("/teams/{team_id}/token-quota/member-usage")
async def get_team_token_quota_member_usage(
    team_id: UUID,
    subject_ids: str = Query(max_length=20_000),
    session: AsyncSession = Depends(session_dep),
    redis: Redis = Depends(redis_dep),
):
    """Current-window per-member usage for the teams drawer. Limits are
    per-member budgets, so the drawer shows each member's own used/limit
    instead of a team aggregate (which would require scanning every member
    counter)."""
    from llm_gateway.services.team_quota import counter_key, current_window

    await _get_or_404(session, Team, team_id)
    ids = [item.strip() for item in subject_ids.split(",") if item.strip()]
    if not ids or len(ids) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="subject_ids: 1-500 个逗号分隔 UUID",
        )
    try:
        parsed = [UUID(item) for item in ids]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_subject_id"
        ) from exc

    result = await session.execute(
        select(TeamTokenQuota).where(col(TeamTokenQuota.team_id) == team_id)
    )
    quota = result.scalar_one_or_none()
    if quota is None or quota.state != ResourceState.ACTIVE:
        return {"team_id": str(team_id), "window": None, "limit": None, "members": []}

    window, window_date, _end = current_window()
    limit_by_window = {
        "morning": quota.morning_tokens,
        "afternoon": quota.afternoon_tokens,
        "evening": quota.evening_tokens,
    }
    limit = limit_by_window[window]
    if limit is None:
        return {"team_id": str(team_id), "window": window, "limit": None, "members": []}

    try:
        used_values = await redis.mget(
            [counter_key(team_id, subject_id, window, window_date) for subject_id in parsed]
        )
    except RedisError:
        used_values = [None] * len(parsed)
    members = [
        {
            "subject_id": str(subject_id),
            "used": int(used) if used is not None else 0,
        }
        for subject_id, used in zip(parsed, used_values, strict=True)
    ]
    return {"team_id": str(team_id), "window": window, "limit": limit, "members": members}


@router.get("/team-token-quotas")
async def list_team_token_quotas(
    q: str | None = Query(default=None, max_length=120),
    team_ids: str | None = Query(default=None, max_length=20_000),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    base = select(TeamTokenQuota, Team.name).outerjoin(
        Team, col(TeamTokenQuota.team_id) == col(Team.id)
    )
    if q and q.strip():
        base = base.where(col(Team.name).ilike(f"%{q.strip()}%"))
    if team_ids and team_ids.strip():
        ids = [item.strip() for item in team_ids.split(",") if item.strip()]
        if len(ids) > 500:
            raise HTTPException(
                status_code=422, detail="too_many_team_ids: 最多 500 个"
            )
        try:
            parsed = [UUID(item) for item in ids]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_team_id") from exc
        base = base.where(col(TeamTokenQuota.team_id).in_(parsed))
    total = await _count_rows(
        session, select(func.count()).select_from(base.subquery())
    )
    rows = (
        await session.execute(
            base.order_by(col(TeamTokenQuota.created_at).desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    quotas = [quota for quota, _name in rows]
    names = {quota.team_id: name for quota, name in rows}
    items = [_quota_row_payload(quota, names.get(quota.team_id)) for quota in quotas]
    return paginated(items, total, limit, offset)


@router.get("/teams/{team_id}/token-quota")
async def get_team_token_quota(
    team_id: UUID,
    session: AsyncSession = Depends(session_dep),
):
    await _get_or_404(session, Team, team_id)
    result = await session.execute(
        select(TeamTokenQuota).where(col(TeamTokenQuota.team_id) == team_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {
            "team_id": str(team_id),
            "team_name": None,
            "morning_tokens": None,
            "afternoon_tokens": None,
            "evening_tokens": None,
            "state": "ACTIVE",
            "current_window": None,
            "current_window_limit": None,
            "current_window_used": None,
        }
    return _quota_row_payload(row)


@router.put("/teams/{team_id}/token-quota")
async def set_team_token_quota(
    team_id: UUID,
    payload: TeamTokenQuotaPayload,
    session: AsyncSession = Depends(session_dep),
):
    await _get_or_404(session, Team, team_id)
    result = await session.execute(
        select(TeamTokenQuota).where(col(TeamTokenQuota.team_id) == team_id)
    )
    quota = result.scalar_one_or_none()
    if quota is None:
        quota = TeamTokenQuota(team_id=team_id)
        session.add(quota)
    quota.morning_tokens = payload.morning_tokens
    quota.afternoon_tokens = payload.afternoon_tokens
    quota.evening_tokens = payload.evening_tokens
    if payload.state is not None:
        quota.state = payload.state
    quota.updated_at = utcnow()
    await session.flush()
    await record_audit_event(
        session,
        action="team.token_quota.set",
        resource_type="team_token_quota",
        resource_id=quota.id,
        outcome="success",
        detail={
            "team_id": str(team_id),
            "morning_tokens": payload.morning_tokens,
            "afternoon_tokens": payload.afternoon_tokens,
            "evening_tokens": payload.evening_tokens,
            "state": payload.state.value if payload.state else None,
        },
    )
    await session.commit()
    await session.refresh(quota)
    return quota


@router.post("/team-memberships")
async def create_team_membership(
    payload: TeamMembershipCreate, session: AsyncSession = Depends(session_dep)
):
    await _get_or_404(session, Team, payload.team_id)
    await _get_or_404(session, Subject, payload.subject_id)
    membership = await ensure_team_membership(
        session,
        team_id=payload.team_id,
        subject_id=payload.subject_id,
        role=payload.role,
    )
    await record_audit_event(
        session,
        action="team_membership.create",
        resource_type="team_membership",
        resource_id=membership.id,
        outcome="success",
    )
    await session.commit()
    await session.refresh(membership)
    return membership


@router.get("/team-memberships")
async def list_team_memberships(
    q: str | None = Query(default=None, max_length=120),
    team_id: UUID | None = Query(default=None),
    state: ResourceState | None = Query(default=None),
    role: str | None = Query(default=None, max_length=40),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    filters = []
    if team_id:
        filters.append(col(TeamMembership.team_id) == team_id)
    if state:
        filters.append(col(TeamMembership.state) == state)
    if role:
        filters.append(col(TeamMembership.role) == role)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        filters.append(
            or_(
                col(Subject.name).ilike(needle),
                col(Subject.login_username).ilike(needle),
            )
        )
    base = (
        select(TeamMembership, Team.name, Subject.name, Subject.login_username)
        .outerjoin(Team, col(TeamMembership.team_id) == col(Team.id))
        .outerjoin(Subject, col(TeamMembership.subject_id) == col(Subject.id))
    )
    if filters:
        base = base.where(*filters)
    total = await _count_rows(
        session, select(func.count()).select_from(base.subquery())
    )
    rows = (
        await session.execute(
            base.order_by(col(TeamMembership.created_at).desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = []
    for membership, team_name, subject_name, subject_login in rows:
        item = membership.model_dump()
        item["team_name"] = team_name
        item["subject_name"] = subject_name
        item["subject_login_username"] = subject_login
        items.append(item)
    return paginated(items, total, limit, offset)


@router.patch("/team-memberships/{membership_id}/state")
async def set_team_membership_state(
    membership_id: UUID,
    payload: StatePatch,
    session: AsyncSession = Depends(session_dep),
):
    membership = await _get_or_404(session, TeamMembership, membership_id)
    membership.state = payload.state
    membership.updated_at = utcnow()
    await record_audit_event(
        session,
        action="team_membership.set_state",
        resource_type="team_membership",
        resource_id=membership.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    return membership


@router.post("/model-team-grants")
async def create_model_team_grant(
    payload: ModelTeamGrantCreate, session: AsyncSession = Depends(session_dep)
):
    await _get_or_404(session, ModelAlias, payload.model_alias_id)
    await _get_or_404(session, Team, payload.team_id)
    grant = await ensure_model_team_grant(
        session,
        model_alias_id=payload.model_alias_id,
        team_id=payload.team_id,
    )
    await record_audit_event(
        session,
        action="model_team_grant.create",
        resource_type="model_team_grant",
        resource_id=grant.id,
        outcome="success",
    )
    await session.commit()
    await session.refresh(grant)
    return grant


@router.get("/model-team-grants")
async def list_model_team_grants(
    team_id: UUID | None = Query(default=None),
    model_alias_id: UUID | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    filters = []
    if team_id:
        filters.append(col(ModelTeamGrant.team_id) == team_id)
    if model_alias_id:
        filters.append(col(ModelTeamGrant.model_alias_id) == model_alias_id)
    base = (
        select(ModelTeamGrant, ModelAlias.alias, Team.name)
        .outerjoin(
            ModelAlias, col(ModelTeamGrant.model_alias_id) == col(ModelAlias.id)
        )
        .outerjoin(Team, col(ModelTeamGrant.team_id) == col(Team.id))
    )
    if filters:
        base = base.where(*filters)
    total = await _count_rows(
        session, select(func.count()).select_from(base.subquery())
    )
    rows = (
        await session.execute(
            base.order_by(col(ModelTeamGrant.created_at).desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = []
    for grant, alias, team_name in rows:
        item = grant.model_dump()
        item["model_alias"] = alias
        item["team_name"] = team_name
        items.append(item)
    return paginated(items, total, limit, offset)


@router.patch("/model-team-grants/{grant_id}/state")
async def set_model_team_grant_state(
    grant_id: UUID,
    payload: StatePatch,
    session: AsyncSession = Depends(session_dep),
):
    grant = await _get_or_404(session, ModelTeamGrant, grant_id)
    grant.state = payload.state
    grant.updated_at = utcnow()
    await record_audit_event(
        session,
        action="model_team_grant.set_state",
        resource_type="model_team_grant",
        resource_id=grant.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    return grant


@router.patch("/model-entitlements/{entitlement_id}/state")
async def set_model_entitlement_state(
    entitlement_id: UUID,
    payload: StatePatch,
    session: AsyncSession = Depends(session_dep),
):
    entitlement = await _get_or_404(session, ModelEntitlement, entitlement_id)
    entitlement.state = payload.state
    entitlement.updated_at = utcnow()
    await record_audit_event(
        session,
        action="model_entitlement.set_state",
        resource_type="model_entitlement",
        resource_id=entitlement.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    return entitlement
