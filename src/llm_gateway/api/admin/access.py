from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.admin._common import (
    StatePatch,
    _audit_update,
    _count_rows,
    _get_or_404,
)
from llm_gateway.api.deps import session_dep
from llm_gateway.db.models import (
    GatewayKey,
    ModelAlias,
    ModelEntitlement,
    Project,
    ResourceState,
    Subject,
    Team,
    TeamMembership,
    ModelTeamGrant,
    utcnow,
)
from llm_gateway.services.resource_payloads import apply_model_patch, paginated
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.security import (
    ensure_model_team_grant,
    ensure_team_membership,
)


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
async def list_model_entitlements(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(
        select(ModelEntitlement).order_by(col(ModelEntitlement.created_at).desc())
    )
    return result.scalars().all()


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
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    stmt = select(Team).order_by(col(Team.name))
    total = await _count_rows(session, select(func.count()).select_from(Team))
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return paginated(rows, total, limit, offset)


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
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    stmt = select(TeamMembership).order_by(col(TeamMembership.created_at).desc())
    total = await _count_rows(session, select(func.count()).select_from(TeamMembership))
    rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return paginated(rows, total, limit, offset)


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
async def list_model_team_grants(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(
        select(ModelTeamGrant).order_by(col(ModelTeamGrant.created_at).desc())
    )
    return result.scalars().all()


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
