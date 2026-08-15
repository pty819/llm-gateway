from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.admin._common import (
    _audit_update,
    _count_rows,
    _detach_upstream_usage,
    _get_or_404,
    _validate_homogeneous_upstream_payload,
)
from llm_gateway.api.deps import redis_dep, session_dep
from llm_gateway.db.models import (
    IPPolicyMode,
    ModelAlias,
    ModelEntitlement,
    ModelTeamGrant,
    ResourceState,
    UpstreamTarget,
)
from llm_gateway.services.resource_payloads import (
    apply_model_patch,
    paginated,
    redact_upstream,
)
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.litellm_client import check_upstream_health
from llm_gateway.services.security import ensure_model_team_grant, get_or_create_team
from llm_gateway.services.upstream_health import filter_unhealthy


router = APIRouter()


class ModelAliasCreate(BaseModel):
    alias: str
    upstream_model_name: str
    litellm_model: str
    sticky_ttl_seconds: int = Field(default=1200, ge=1, le=86400)
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_reasoning: bool = True
    ip_policy_mode: IPPolicyMode = IPPolicyMode.ALL_PASS
    ip_allowlist_cidrs: list[str] = Field(default_factory=list)
    notes: str | None = None


class ModelAliasUpdate(BaseModel):
    upstream_model_name: str | None = None
    litellm_model: str | None = None
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
    await ensure_model_team_grant(
        session, model_alias_id=model_alias.id, team_id=admin_team.id
    )
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
    # Register with LiteLLM so /v1/responses uses real SSE streaming instead
    # of fake-streaming (custom vLLM model names are not in LiteLLM's registry
    # and would otherwise trigger the non-streaming fallback).
    from llm_gateway.services.litellm_client import (
        register_model_for_native_streaming,
    )

    register_model_for_native_streaming(model_alias)
    return model_alias


@router.get("/model-aliases")
async def list_model_aliases(
    q: str | None = Query(default=None, max_length=120),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    stmt = select(ModelAlias)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                col(ModelAlias.alias).ilike(needle),
                col(ModelAlias.upstream_model_name).ilike(needle),
            )
        )
    total = await _count_rows(
        session, select(func.count()).select_from(stmt.subquery())
    )
    rows = (
        (
            await session.execute(
                stmt.order_by(col(ModelAlias.created_at).desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return paginated(rows, total, limit, offset)


@router.get("/model-aliases/options")
async def list_model_alias_options(
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(session_dep),
):
    """Lightweight id/alias pairs for searchable pickers; avoids shipping
    full model rows (CIDR lists, notes...) to build dropdown options."""
    stmt = select(ModelAlias.id, ModelAlias.alias).order_by(col(ModelAlias.alias))
    if q and q.strip():
        stmt = stmt.where(col(ModelAlias.alias).ilike(f"%{q.strip()}%"))
    rows = (await session.execute(stmt.limit(limit))).all()
    return [{"id": str(row.id), "alias": row.alias} for row in rows]


@router.patch("/model-aliases/{model_alias_id}")
async def update_model_alias(
    model_alias_id: UUID,
    payload: ModelAliasUpdate,
    session: AsyncSession = Depends(session_dep),
):
    model_alias = await _get_or_404(session, ModelAlias, model_alias_id)
    apply_model_patch(model_alias, payload)
    await _audit_update(
        session, "model_alias.update", "model_alias", model_alias.id, payload
    )
    await session.commit()
    await session.refresh(model_alias)
    # Re-register in case litellm_model changed - the registry key is derived
    # from it, so a rename needs a fresh entry to keep real streaming working.
    from llm_gateway.services.litellm_client import (
        register_model_for_native_streaming,
    )

    register_model_for_native_streaming(model_alias)
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
                select(UpstreamTarget).where(
                    col(UpstreamTarget.model_alias_id) == model_alias.id
                )
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
                "upstreams": [
                    {"id": str(item.id), "name": item.name} for item in upstreams
                ],
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
        delete(ModelEntitlement).where(
            col(ModelEntitlement.model_alias_id) == model_alias.id
        )
    )
    await session.execute(
        delete(ModelTeamGrant).where(
            col(ModelTeamGrant.model_alias_id) == model_alias.id
        )
    )
    await session.execute(
        delete(UpstreamTarget).where(
            col(UpstreamTarget.model_alias_id) == model_alias.id
        )
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
async def list_upstreams(
    q: str | None = Query(default=None, max_length=120),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
    redis: Redis = Depends(redis_dep),
):
    filters = []
    if q and q.strip():
        needle = f"%{q.strip()}%"
        filters.append(
            or_(
                col(UpstreamTarget.name).ilike(needle),
                col(UpstreamTarget.base_url).ilike(needle),
            )
        )
    count_stmt = select(func.count()).select_from(UpstreamTarget)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = await _count_rows(session, count_stmt)
    stmt = (
        select(UpstreamTarget, ModelAlias.alias)
        .outerjoin(ModelAlias, col(UpstreamTarget.model_alias_id) == col(ModelAlias.id))
        .order_by(col(UpstreamTarget.created_at).desc())
        .offset(offset)
        .limit(limit)
    )
    if filters:
        stmt = stmt.where(*filters)
    rows = (await session.execute(stmt)).all()
    # Runtime liveness from the sidecar's Redis markers — `state` alone never
    # reflected it, so dead endpoints always showed "active" in the UI even
    # while being routed around.
    unhealthy_ids = await filter_unhealthy(redis, [upstream.id for upstream, _ in rows])
    items = []
    for upstream, alias in rows:
        item = redact_upstream(upstream)
        item["model_alias"] = alias
        item["runtime_healthy"] = str(upstream.id) not in unhealthy_ids
        items.append(item)
    return paginated(items, total, limit, offset)


@router.get("/upstreams/options")
async def list_upstream_options(
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(session_dep),
):
    """Minimal upstream identity rows for the realtime lock view, so the
    metrics page doesn't need the full paginated upstream list."""
    stmt = (
        select(
            UpstreamTarget.id,
            UpstreamTarget.name,
            UpstreamTarget.state,
            ModelAlias.alias,
        )
        .outerjoin(ModelAlias, col(UpstreamTarget.model_alias_id) == col(ModelAlias.id))
        .order_by(col(UpstreamTarget.name))
    )
    if q and q.strip():
        stmt = stmt.where(col(UpstreamTarget.name).ilike(f"%{q.strip()}%"))
    rows = (await session.execute(stmt.limit(limit))).all()
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "state": row.state.value,
            "model_alias": row.alias,
        }
        for row in rows
    ]


@router.get("/upstreams/{upstream_id}/health")
async def upstream_health(
    upstream_id: UUID, session: AsyncSession = Depends(session_dep)
):
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
    await _validate_homogeneous_upstream_payload(
        session, payload=payload, existing=upstream
    )
    apply_model_patch(upstream, payload)
    await _audit_update(
        session, "upstream.update", "upstream_target", upstream.id, payload
    )
    await session.commit()
    await session.refresh(upstream)
    return redact_upstream(upstream)


@router.delete("/upstreams/{upstream_id}")
async def delete_upstream(
    upstream_id: UUID, session: AsyncSession = Depends(session_dep)
):
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
