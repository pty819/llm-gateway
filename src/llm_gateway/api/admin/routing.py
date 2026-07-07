from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.admin._common import (
    _audit_update,
    _detach_upstream_usage,
    _get_or_404,
    _validate_homogeneous_upstream_payload,
)
from llm_gateway.api.deps import session_dep
from llm_gateway.db.models import (
    IPPolicyMode,
    ModelAlias,
    ModelEntitlement,
    ModelTeamGrant,
    ResourceState,
    RouterCommandConfig,
    RouterPolicy,
    UpstreamTarget,
)
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.resource_payloads import apply_model_patch, redact_upstream
from llm_gateway.services.router_command import render_router_command
from llm_gateway.services.security import ensure_model_team_grant, get_or_create_team
from llm_gateway.services.upstream_client import check_upstream_health

router = APIRouter()


class ModelAliasCreate(BaseModel):
    alias: str
    upstream_model_name: str
    sticky_ttl_seconds: int = Field(default=1200, ge=1, le=86400)
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_reasoning: bool = True
    ip_policy_mode: IPPolicyMode = IPPolicyMode.ALL_PASS
    ip_allowlist_cidrs: list[str] = Field(default_factory=list)
    notes: str | None = None


class ModelAliasUpdate(BaseModel):
    upstream_model_name: str | None = None
    sticky_ttl_seconds: int | None = Field(default=None, ge=1, le=86400)
    supports_streaming: bool | None = None
    supports_tools: bool | None = None
    supports_reasoning: bool | None = None
    ip_policy_mode: IPPolicyMode | None = None
    ip_allowlist_cidrs: list[str] | None = None
    notes: str | None = None
    state: ResourceState | None = None


class UpstreamTargetCreate(BaseModel):
    model_alias_id: UUID
    name: str
    base_url: str
    metrics_url: str | None = None
    api_key_ref: str | None = None
    api_key_value: str | None = None
    health_path: str = "/models"
    extra_headers: dict[str, str] = Field(default_factory=dict)


class UpstreamTargetUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    metrics_url: str | None = None
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


@router.post("/model-aliases")
async def create_model_alias(
    payload: ModelAliasCreate, session: AsyncSession = Depends(session_dep)
):
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
    result = await session.execute(select(ModelAlias).order_by(col(ModelAlias.created_at).desc()))
    return result.scalars().all()


@router.patch("/model-aliases/{model_alias_id}")
async def update_model_alias(
    model_alias_id: UUID,
    payload: ModelAliasUpdate,
    session: AsyncSession = Depends(session_dep),
):
    model_alias = await _get_or_404(session, ModelAlias, model_alias_id)
    apply_model_patch(model_alias, payload)
    await _audit_update(session, "model_alias.update", "model_alias", model_alias.id, payload)
    await session.commit()
    await session.refresh(model_alias)
    return model_alias


@router.delete("/model-aliases/{model_alias_id}")
async def delete_model_alias(
    model_alias_id: UUID,
    cascade_upstreams: bool = Query(default=False),
    session: AsyncSession = Depends(session_dep),
):
    model_alias = await _get_or_404(session, ModelAlias, model_alias_id)
    upstreams = (
        (
            await session.execute(
                select(UpstreamTarget).where(col(UpstreamTarget.model_alias_id) == model_alias.id)
            )
        )
        .scalars()
        .all()
    )
    if upstreams and not cascade_upstreams:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "model_alias_has_upstreams",
                "upstream_count": len(upstreams),
                "upstreams": [{"id": str(item.id), "name": item.name} for item in upstreams],
            },
        )
    await record_audit_event(
        session,
        action="model_alias.delete",
        resource_type="model_alias",
        resource_id=model_alias.id,
        outcome="success",
        detail={
            "alias": model_alias.alias,
            "cascade_upstreams": cascade_upstreams,
            "upstream_count": len(upstreams),
        },
    )
    detached_usage_count = 0
    for upstream in upstreams:
        detached_usage_count += await _detach_upstream_usage(session, upstream)
    await session.execute(
        delete(ModelEntitlement).where(col(ModelEntitlement.model_alias_id) == model_alias.id)
    )
    await session.execute(
        delete(ModelTeamGrant).where(col(ModelTeamGrant.model_alias_id) == model_alias.id)
    )
    await session.execute(
        delete(RouterCommandConfig).where(col(RouterCommandConfig.model_alias_id) == model_alias.id)
    )
    await session.execute(
        delete(UpstreamTarget).where(col(UpstreamTarget.model_alias_id) == model_alias.id)
    )
    await session.delete(model_alias)
    await session.commit()
    return {
        "ok": True,
        "deleted_upstreams": len(upstreams),
        "detached_usage_facts": detached_usage_count,
    }


@router.post("/upstreams")
async def create_upstream(
    payload: UpstreamTargetCreate, session: AsyncSession = Depends(session_dep)
):
    await _get_or_404(session, ModelAlias, payload.model_alias_id)
    await _validate_homogeneous_upstream_payload(session, payload=payload)
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
    return redact_upstream(upstream)


@router.get("/upstreams")
async def list_upstreams(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(
        select(UpstreamTarget).order_by(col(UpstreamTarget.created_at).desc())
    )
    return [redact_upstream(item) for item in result.scalars().all()]


@router.get("/upstreams/{upstream_id}/health")
async def upstream_health(upstream_id: UUID, session: AsyncSession = Depends(session_dep)):
    upstream = await _get_or_404(session, UpstreamTarget, upstream_id)
    result = await check_upstream_health(upstream)
    return {"upstream": redact_upstream(upstream), "health": result}


@router.patch("/upstreams/{upstream_id}")
async def update_upstream(
    upstream_id: UUID,
    payload: UpstreamTargetUpdate,
    session: AsyncSession = Depends(session_dep),
):
    upstream = await _get_or_404(session, UpstreamTarget, upstream_id)
    await _validate_homogeneous_upstream_payload(session, payload=payload, existing=upstream)
    apply_model_patch(upstream, payload)
    await _audit_update(session, "upstream.update", "upstream_target", upstream.id, payload)
    await session.commit()
    await session.refresh(upstream)
    return redact_upstream(upstream)


@router.delete("/upstreams/{upstream_id}")
async def delete_upstream(upstream_id: UUID, session: AsyncSession = Depends(session_dep)):
    upstream = await _get_or_404(session, UpstreamTarget, upstream_id)
    detached_usage_count = await _detach_upstream_usage(session, upstream)
    await record_audit_event(
        session,
        action="upstream.delete",
        resource_type="upstream_target",
        resource_id=upstream.id,
        outcome="success",
        detail={
            "name": upstream.name,
            "base_url": upstream.base_url,
            "detached_usage_facts": detached_usage_count,
        },
    )
    await session.delete(upstream)
    await session.commit()
    return {"ok": True, "detached_usage_facts": detached_usage_count}


@router.post("/router-command-configs")
async def create_router_command_config(
    payload: RouterCommandConfigCreate, session: AsyncSession = Depends(session_dep)
):
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
    result = await session.execute(
        select(RouterCommandConfig).order_by(col(RouterCommandConfig.created_at).desc())
    )
    configs = result.scalars().all()
    return [{"config": config, "command": render_router_command(config)} for config in configs]


@router.patch("/router-command-configs/{config_id}")
async def update_router_command_config(
    config_id: UUID,
    payload: RouterCommandConfigUpdate,
    session: AsyncSession = Depends(session_dep),
):
    config = await _get_or_404(session, RouterCommandConfig, config_id)
    apply_model_patch(config, payload)
    await _audit_update(
        session,
        "router_command_config.update",
        "router_command_config",
        config.id,
        payload,
    )
    await session.commit()
    await session.refresh(config)
    return {"config": config, "command": render_router_command(config)}
