from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlmodel import col

from llm_gateway.api.deps import admin_dep, redis_dep
from llm_gateway.db.models import ModelAlias, ResourceState, UpstreamTarget
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.runtime_metrics import VLLMMetricsTarget, runtime_snapshot
from llm_gateway.services.upstream_health import filter_unhealthy as filter_unhealthy_upstreams


router = APIRouter(prefix="/admin")


@router.get("/realtime/snapshot", dependencies=[Depends(admin_dep)])
async def realtime_snapshot(
    window_seconds: int = Query(default=10, ge=1, le=300),
    redis: Redis = Depends(redis_dep),
):
    targets = await _load_vllm_metric_targets(redis)
    return await runtime_snapshot(
        redis, window_seconds=window_seconds, vllm_targets=targets
    )


@router.get("/realtime/stream", dependencies=[Depends(admin_dep)])
async def realtime_stream(
    request: Request,
    window_seconds: int = Query(default=10, ge=1, le=300),
    interval_seconds: float = Query(default=1.0, ge=0.5, le=10.0),
    redis: Redis = Depends(redis_dep),
):
    async def events():
        while not await request.is_disconnected():
            targets = await _load_vllm_metric_targets(redis)
            snapshot = await runtime_snapshot(
                redis, window_seconds=window_seconds, vllm_targets=targets
            )
            payload = json.dumps(
                jsonable_encoder(snapshot),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"event: metrics\ndata: {payload}\n\n"
            await asyncio.sleep(interval_seconds)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


async def _load_vllm_metric_targets(
    redis: Redis | None = None,
) -> list[VLLMMetricsTarget]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(UpstreamTarget, ModelAlias)
                .join(
                    ModelAlias, col(ModelAlias.id) == col(UpstreamTarget.model_alias_id)
                )
                .where(
                    col(UpstreamTarget.state) == ResourceState.ACTIVE,
                    col(ModelAlias.state) == ResourceState.ACTIVE,
                )
                .order_by(col(UpstreamTarget.created_at).desc())
            )
        ).all()
        # Apply runtime liveness filter so the realtime dashboard only polls
        # metrics for upstreams the sidecar currently considers reachable.
        # Degrades open when Redis is unavailable.
        if redis is not None and rows:
            unhealthy_ids = await filter_unhealthy_upstreams(
                redis, [upstream.id for upstream, _ in rows]
            )
            if unhealthy_ids:
                rows = [
                    (upstream, model)
                    for upstream, model in rows
                    if str(upstream.id) not in unhealthy_ids
                ]
        targets = [
            VLLMMetricsTarget(
                upstream_id=str(upstream.id),
                upstream_name=upstream.name,
                model_alias=model.alias,
                base_url=upstream.base_url,
                extra_headers=dict(upstream.extra_headers or {}),
                api_key=upstream.api_key_value or upstream.api_key_ref,
                metrics_url=upstream.metrics_url,
            )
            for upstream, model in rows
        ]
        await session.rollback()
    return targets
