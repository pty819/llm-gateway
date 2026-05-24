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
    RequestFact,
    RequestOutcome,
    ResourceState,
    RouterCommandConfig,
    RouterPolicy,
    Subject,
    SubjectType,
    UpstreamTarget,
    utcnow,
)
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.router_command import render_router_command
from llm_gateway.services.security import create_gateway_key


router = APIRouter(prefix="/admin", dependencies=[Depends(admin_dep)])


class SubjectCreate(BaseModel):
    name: str
    type: SubjectType
    notes: str | None = None


class ProjectCreate(BaseModel):
    name: str
    owner_subject_id: UUID | None = None
    notes: str | None = None


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


class ModelEntitlementCreate(BaseModel):
    model_alias_id: UUID
    subject_id: UUID | None = None
    project_id: UUID | None = None
    gateway_key_id: UUID | None = None


class UpstreamTargetCreate(BaseModel):
    model_alias_id: UUID
    name: str
    base_url: str
    api_key_ref: str | None = None
    api_key_value: str | None = None
    health_path: str = "/models"
    extra_headers: dict[str, str] = Field(default_factory=dict)


class RouterCommandConfigCreate(BaseModel):
    model_alias_id: UUID
    name: str
    worker_urls: list[str]
    policy: RouterPolicy = RouterPolicy.CONSISTENT_HASH
    host: str = "0.0.0.0"
    port: int
    extra_args: dict[str, Any] = Field(default_factory=dict)


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
