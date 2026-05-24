from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.api.deps import admin_dep, session_dep
from llm_gateway.db.models import (
    AuditEvent,
    GatewayKey,
    IPPolicyMode,
    ModelAlias,
    ModelEntitlement,
    Project,
    ProjectMembership,
    RatePolicy,
    RequestFact,
    RequestOutcome,
    ResourceState,
    RouterCommandConfig,
    RouterPolicy,
    Subject,
    SubjectType,
    Team,
    TeamMembership,
    ModelTeamGrant,
    UpstreamTarget,
    utcnow,
)
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.litellm_client import check_upstream_health
from llm_gateway.services.router_command import render_router_command
from llm_gateway.services.security import (
    create_gateway_key,
    ensure_model_team_grant,
    ensure_team_membership,
    get_or_create_team,
)


router = APIRouter(prefix="/admin", dependencies=[Depends(admin_dep)])


class SubjectCreate(BaseModel):
    name: str
    type: SubjectType
    notes: str | None = None


class ProjectCreate(BaseModel):
    name: str
    owner_subject_id: UUID | None = None
    notes: str | None = None


class SubjectUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    owner_subject_id: UUID | None = None
    notes: str | None = None


class ProjectMembershipCreate(BaseModel):
    project_id: UUID
    subject_id: UUID
    role: str = "member"


class GatewayKeyCreate(BaseModel):
    subject_id: UUID
    project_id: UUID
    name: str


class ModelAliasCreate(BaseModel):
    alias: str
    upstream_model_name: str
    litellm_model: str
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_reasoning: bool = True
    ip_policy_mode: IPPolicyMode = IPPolicyMode.ALL_PASS
    ip_allowlist_cidrs: list[str] = Field(default_factory=list)
    notes: str | None = None


class ModelAliasUpdate(BaseModel):
    upstream_model_name: str | None = None
    litellm_model: str | None = None
    supports_streaming: bool | None = None
    supports_tools: bool | None = None
    supports_reasoning: bool | None = None
    ip_policy_mode: IPPolicyMode | None = None
    ip_allowlist_cidrs: list[str] | None = None
    notes: str | None = None
    state: ResourceState | None = None


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


class UpstreamTargetCreate(BaseModel):
    model_alias_id: UUID
    name: str
    base_url: str
    api_key_ref: str | None = None
    api_key_value: str | None = None
    health_path: str = "/models"
    extra_headers: dict[str, str] = Field(default_factory=dict)


class UpstreamTargetUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key_ref: str | None = None
    api_key_value: str | None = None
    health_path: str | None = None
    extra_headers: dict[str, str] | None = None
    state: ResourceState | None = None


class RouterCommandConfigCreate(BaseModel):
    model_alias_id: UUID
    name: str
    worker_urls: list[str]
    policy: RouterPolicy = RouterPolicy.CONSISTENT_HASH
    host: str = "0.0.0.0"
    port: int
    extra_args: dict[str, Any] = Field(default_factory=dict)


class RouterCommandConfigUpdate(BaseModel):
    name: str | None = None
    worker_urls: list[str] | None = None
    policy: RouterPolicy | None = None
    host: str | None = None
    port: int | None = None
    extra_args: dict[str, Any] | None = None


class RatePolicyCreate(BaseModel):
    scope: str
    scope_id: UUID
    requests_per_minute: int | None = None
    concurrency_limit: int | None = None


class RatePolicyUpdate(BaseModel):
    requests_per_minute: int | None = None
    concurrency_limit: int | None = None
    state: ResourceState | None = None


class StatePatch(BaseModel):
    state: ResourceState


@router.post("/subjects")
async def create_subject(payload: SubjectCreate, session: AsyncSession = Depends(session_dep)):
    subject = Subject(**payload.model_dump())
    session.add(subject)
    await session.flush()
    await record_audit_event(
        session,
        action="subject.create",
        resource_type="subject",
        resource_id=subject.id,
        outcome="success",
    )
    await session.commit()
    await session.refresh(subject)
    return subject


@router.get("/subjects")
async def list_subjects(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(Subject).order_by(Subject.created_at.desc()))
    return result.scalars().all()


@router.patch("/subjects/{subject_id}")
async def update_subject(subject_id: UUID, payload: SubjectUpdate, session: AsyncSession = Depends(session_dep)):
    subject = await _get_or_404(session, Subject, subject_id)
    _apply_patch(subject, payload)
    await _audit_update(session, "subject.update", "subject", subject.id, payload)
    await session.commit()
    await session.refresh(subject)
    return subject


@router.patch("/subjects/{subject_id}/state")
async def set_subject_state(subject_id: UUID, payload: StatePatch, session: AsyncSession = Depends(session_dep)):
    subject = await _get_or_404(session, Subject, subject_id)
    subject.state = payload.state
    subject.updated_at = utcnow()
    await record_audit_event(
        session,
        action="subject.set_state",
        resource_type="subject",
        resource_id=subject.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    return subject


@router.post("/projects")
async def create_project(payload: ProjectCreate, session: AsyncSession = Depends(session_dep)):
    project = Project(**payload.model_dump())
    session.add(project)
    await session.flush()
    await record_audit_event(
        session,
        action="project.create",
        resource_type="project",
        resource_id=project.id,
        outcome="success",
    )
    await session.commit()
    await session.refresh(project)
    return project


@router.get("/projects")
async def list_projects(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(Project).order_by(Project.created_at.desc()))
    return result.scalars().all()


@router.patch("/projects/{project_id}")
async def update_project(project_id: UUID, payload: ProjectUpdate, session: AsyncSession = Depends(session_dep)):
    project = await _get_or_404(session, Project, project_id)
    if payload.owner_subject_id:
        await _get_or_404(session, Subject, payload.owner_subject_id)
    _apply_patch(project, payload)
    await _audit_update(session, "project.update", "project", project.id, payload)
    await session.commit()
    await session.refresh(project)
    return project


@router.post("/project-memberships")
async def create_project_membership(payload: ProjectMembershipCreate, session: AsyncSession = Depends(session_dep)):
    await _get_or_404(session, Project, payload.project_id)
    await _get_or_404(session, Subject, payload.subject_id)
    membership = ProjectMembership(**payload.model_dump())
    session.add(membership)
    await session.flush()
    await record_audit_event(
        session,
        action="project_membership.create",
        resource_type="project_membership",
        resource_id=membership.id,
        outcome="success",
    )
    await session.commit()
    await session.refresh(membership)
    return membership


@router.get("/project-memberships")
async def list_project_memberships(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(ProjectMembership).order_by(ProjectMembership.created_at.desc()))
    return result.scalars().all()


@router.post("/gateway-keys")
async def issue_gateway_key(payload: GatewayKeyCreate, session: AsyncSession = Depends(session_dep)):
    await _get_or_404(session, Subject, payload.subject_id)
    await _get_or_404(session, Project, payload.project_id)
    key, raw_key = await create_gateway_key(
        session,
        subject_id=payload.subject_id,
        project_id=payload.project_id,
        name=payload.name,
    )
    await record_audit_event(
        session,
        action="gateway_key.issue",
        resource_type="gateway_key",
        resource_id=key.id,
        outcome="success",
        detail={"key_prefix": key.key_prefix},
    )
    await session.commit()
    await session.refresh(key)
    return {"key": _redact_gateway_key(key), "plaintext_key": raw_key}


@router.get("/gateway-keys")
async def list_gateway_keys(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(GatewayKey).order_by(GatewayKey.created_at.desc()))
    return [_redact_gateway_key(item) for item in result.scalars().all()]


@router.patch("/gateway-keys/{gateway_key_id}/state")
async def set_gateway_key_state(gateway_key_id: UUID, payload: StatePatch, session: AsyncSession = Depends(session_dep)):
    key = await _get_or_404(session, GatewayKey, gateway_key_id)
    key.state = payload.state
    key.updated_at = utcnow()
    await record_audit_event(
        session,
        action="gateway_key.set_state",
        resource_type="gateway_key",
        resource_id=key.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    return _redact_gateway_key(key)


@router.post("/model-aliases")
async def create_model_alias(payload: ModelAliasCreate, session: AsyncSession = Depends(session_dep)):
    model_alias = ModelAlias(**payload.model_dump())
    session.add(model_alias)
    await session.flush()
    admin_team = await get_or_create_team(
        session,
        name="admin",
        notes="Built-in administrators with access to all models.",
        is_builtin=True,
    )
    await ensure_model_team_grant(session, model_alias_id=model_alias.id, team_id=admin_team.id)
    await record_audit_event(
        session,
        action="model_alias.create",
        resource_type="model_alias",
        resource_id=model_alias.id,
        outcome="success",
        detail={"alias": model_alias.alias},
    )
    await session.commit()
    await session.refresh(model_alias)
    return model_alias


@router.get("/model-aliases")
async def list_model_aliases(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(ModelAlias).order_by(ModelAlias.created_at.desc()))
    return result.scalars().all()


@router.patch("/model-aliases/{model_alias_id}")
async def update_model_alias(
    model_alias_id: UUID,
    payload: ModelAliasUpdate,
    session: AsyncSession = Depends(session_dep),
):
    model_alias = await _get_or_404(session, ModelAlias, model_alias_id)
    _apply_patch(model_alias, payload)
    await _audit_update(session, "model_alias.update", "model_alias", model_alias.id, payload)
    await session.commit()
    await session.refresh(model_alias)
    return model_alias


@router.post("/model-entitlements")
async def create_model_entitlement(payload: ModelEntitlementCreate, session: AsyncSession = Depends(session_dep)):
    if not any([payload.subject_id, payload.project_id, payload.gateway_key_id]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="entitlement_scope_required")
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
    result = await session.execute(select(ModelEntitlement).order_by(ModelEntitlement.created_at.desc()))
    return result.scalars().all()


@router.post("/teams")
async def create_team(payload: TeamCreate, session: AsyncSession = Depends(session_dep)):
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
async def list_teams(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(Team).order_by(Team.name))
    return result.scalars().all()


@router.patch("/teams/{team_id}")
async def update_team(team_id: UUID, payload: TeamUpdate, session: AsyncSession = Depends(session_dep)):
    team = await _get_or_404(session, Team, team_id)
    _apply_patch(team, payload)
    await _audit_update(session, "team.update", "team", team.id, payload)
    await session.commit()
    await session.refresh(team)
    return team


@router.post("/team-memberships")
async def create_team_membership(payload: TeamMembershipCreate, session: AsyncSession = Depends(session_dep)):
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
async def list_team_memberships(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(TeamMembership).order_by(TeamMembership.created_at.desc()))
    return result.scalars().all()


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
async def create_model_team_grant(payload: ModelTeamGrantCreate, session: AsyncSession = Depends(session_dep)):
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
    result = await session.execute(select(ModelTeamGrant).order_by(ModelTeamGrant.created_at.desc()))
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


@router.post("/upstreams")
async def create_upstream(payload: UpstreamTargetCreate, session: AsyncSession = Depends(session_dep)):
    await _get_or_404(session, ModelAlias, payload.model_alias_id)
    upstream = UpstreamTarget(**payload.model_dump())
    session.add(upstream)
    await session.flush()
    await record_audit_event(
        session,
        action="upstream.create",
        resource_type="upstream_target",
        resource_id=upstream.id,
        outcome="success",
        detail={"base_url": upstream.base_url, "name": upstream.name},
    )
    await session.commit()
    await session.refresh(upstream)
    return _redact_upstream(upstream)


@router.get("/upstreams")
async def list_upstreams(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(UpstreamTarget).order_by(UpstreamTarget.created_at.desc()))
    return [_redact_upstream(item) for item in result.scalars().all()]


@router.get("/upstreams/{upstream_id}/health")
async def upstream_health(upstream_id: UUID, session: AsyncSession = Depends(session_dep)):
    upstream = await _get_or_404(session, UpstreamTarget, upstream_id)
    result = await check_upstream_health(upstream)
    return {"upstream": _redact_upstream(upstream), "health": result}


@router.patch("/upstreams/{upstream_id}")
async def update_upstream(
    upstream_id: UUID,
    payload: UpstreamTargetUpdate,
    session: AsyncSession = Depends(session_dep),
):
    upstream = await _get_or_404(session, UpstreamTarget, upstream_id)
    _apply_patch(upstream, payload)
    await _audit_update(session, "upstream.update", "upstream_target", upstream.id, payload)
    await session.commit()
    await session.refresh(upstream)
    return _redact_upstream(upstream)


@router.post("/router-command-configs")
async def create_router_command_config(payload: RouterCommandConfigCreate, session: AsyncSession = Depends(session_dep)):
    await _get_or_404(session, ModelAlias, payload.model_alias_id)
    config = RouterCommandConfig(**payload.model_dump())
    session.add(config)
    await session.flush()
    await record_audit_event(
        session,
        action="router_command_config.create",
        resource_type="router_command_config",
        resource_id=config.id,
        outcome="success",
        detail={"policy": config.policy.value, "port": config.port},
    )
    await session.commit()
    await session.refresh(config)
    return {"config": config, "command": render_router_command(config)}


@router.get("/router-command-configs")
async def list_router_command_configs(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(RouterCommandConfig).order_by(RouterCommandConfig.created_at.desc()))
    configs = result.scalars().all()
    return [{"config": config, "command": render_router_command(config)} for config in configs]


@router.patch("/router-command-configs/{config_id}")
async def update_router_command_config(
    config_id: UUID,
    payload: RouterCommandConfigUpdate,
    session: AsyncSession = Depends(session_dep),
):
    config = await _get_or_404(session, RouterCommandConfig, config_id)
    _apply_patch(config, payload)
    await _audit_update(session, "router_command_config.update", "router_command_config", config.id, payload)
    await session.commit()
    await session.refresh(config)
    return {"config": config, "command": render_router_command(config)}


@router.post("/rate-policies")
async def create_rate_policy(payload: RatePolicyCreate, session: AsyncSession = Depends(session_dep)):
    policy = RatePolicy(**payload.model_dump())
    session.add(policy)
    await session.flush()
    await record_audit_event(
        session,
        action="rate_policy.create",
        resource_type="rate_policy",
        resource_id=policy.id,
        outcome="success",
        detail={"scope": policy.scope, "scope_id": str(policy.scope_id)},
    )
    await session.commit()
    await session.refresh(policy)
    return policy


@router.get("/rate-policies")
async def list_rate_policies(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(RatePolicy).order_by(RatePolicy.created_at.desc()))
    return result.scalars().all()


@router.patch("/rate-policies/{policy_id}")
async def update_rate_policy(
    policy_id: UUID,
    payload: RatePolicyUpdate,
    session: AsyncSession = Depends(session_dep),
):
    policy = await _get_or_404(session, RatePolicy, policy_id)
    _apply_patch(policy, payload)
    await _audit_update(session, "rate_policy.update", "rate_policy", policy.id, payload)
    await session.commit()
    await session.refresh(policy)
    return policy


@router.get("/usage/summary")
async def usage_summary(
    start: datetime | None = None,
    end: datetime | None = None,
    session: AsyncSession = Depends(session_dep),
):
    filters = []
    if start:
        filters.append(RequestFact.started_at >= start)
    if end:
        filters.append(RequestFact.started_at < end)

    success_count = func.sum(case((RequestFact.outcome == RequestOutcome.SUCCESS, 1), else_=0)).label("success_count")
    failure_count = func.sum(case((RequestFact.outcome != RequestOutcome.SUCCESS, 1), else_=0)).label("failure_count")
    stmt = (
        select(
            RequestFact.model_alias,
            RequestFact.subject_id,
            RequestFact.project_id,
            func.count(RequestFact.id).label("request_count"),
            func.coalesce(func.sum(RequestFact.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(RequestFact.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(RequestFact.total_tokens), 0).label("total_tokens"),
            success_count,
            failure_count,
        )
        .where(*filters)
        .group_by(RequestFact.model_alias, RequestFact.subject_id, RequestFact.project_id)
    )
    rows = (await session.execute(stmt)).mappings().all()
    return [dict(row) for row in rows]


@router.get("/usage/ranking")
async def usage_ranking(
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    limit: int = 20,
    session: AsyncSession = Depends(session_dep),
):
    filters = [RequestFact.subject_id.isnot(None)]
    if start:
        filters.append(RequestFact.started_at >= start)
    if end:
        filters.append(RequestFact.started_at < end)
    if model:
        filters.append(RequestFact.model_alias == model)

    stmt = (
        select(
            RequestFact.subject_id,
            Subject.login_username,
            Subject.name.label("subject_name"),
            func.count(RequestFact.id).label("request_count"),
            func.coalesce(func.sum(RequestFact.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(RequestFact.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(RequestFact.total_tokens), 0).label("total_tokens"),
        )
        .join(Subject, RequestFact.subject_id == Subject.id)
        .where(*filters)
        .group_by(RequestFact.subject_id, Subject.login_username, Subject.name)
        .order_by(func.coalesce(func.sum(RequestFact.total_tokens), 0).desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).mappings().all()
    return [dict(row) for row in rows]


@router.get("/audit-events")
async def list_audit_events(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(200))
    return result.scalars().all()


async def _get_or_404(session: AsyncSession, model, item_id: UUID):
    item = await session.get(model, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{model.__name__}_not_found")
    return item


def _redact_upstream(upstream: UpstreamTarget) -> dict[str, Any]:
    data = upstream.model_dump()
    data["api_key_value"] = None
    data["has_api_key"] = bool(upstream.api_key_value or upstream.api_key_ref)
    return data


def _redact_gateway_key(key: GatewayKey) -> dict[str, Any]:
    data = key.model_dump()
    data["key_hash"] = None
    return data


def _apply_patch(target, payload: BaseModel) -> None:
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(target, key, value)
    target.updated_at = utcnow()


async def _audit_update(
    session: AsyncSession,
    action: str,
    resource_type: str,
    resource_id: UUID,
    payload: BaseModel,
) -> None:
    await record_audit_event(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="success",
        detail=payload.model_dump(exclude_unset=True, mode="json"),
    )
