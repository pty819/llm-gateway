from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlmodel import col

from llm_gateway.api.deps import redis_dep, settings_dep
from llm_gateway.core.config import Settings
from llm_gateway.db.models import ModelAlias, ResourceState, UpstreamTarget
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.runtime_metrics import VLLMMetricsTarget, runtime_snapshot
from llm_gateway.services.security import (
    authenticate_user_session,
    ensure_builtin_identity,
)


router = APIRouter(prefix="/admin")


@router.get("/realtime/snapshot")
async def realtime_snapshot(
    request: Request,
    window_seconds: int = Query(default=10, ge=1, le=300),
    x_admin_token: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    redis: Redis = Depends(redis_dep),
    settings: Settings = Depends(settings_dep),
):
    await _require_admin(
        request=request,
        settings=settings,
        x_admin_token=x_admin_token,
        x_session_token=x_session_token,
    )
    targets = await _load_vllm_metric_targets()
    return await runtime_snapshot(
        redis, window_seconds=window_seconds, vllm_targets=targets
    )


@router.get("/realtime/stream")
async def realtime_stream(
    request: Request,
    window_seconds: int = Query(default=10, ge=1, le=300),
    interval_seconds: float = Query(default=1.0, ge=0.5, le=10.0),
    x_admin_token: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    redis: Redis = Depends(redis_dep),
    settings: Settings = Depends(settings_dep),
):
    await _require_admin(
        request=request,
        settings=settings,
        x_admin_token=x_admin_token,
        x_session_token=x_session_token,
    )
    targets = await _load_vllm_metric_targets()

    async def events():
        while not await request.is_disconnected():
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


async def _require_admin(
    *,
    request: Request,
    settings: Settings,
    x_admin_token: str | None,
    x_session_token: str | None,
) -> None:
    if x_admin_token and x_admin_token == settings.admin_token:
        return

    raw_token = x_session_token or _session_token(request)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_admin_token",
        )

    async with AsyncSessionLocal() as session:
        await ensure_builtin_identity(session, settings)
        await session.commit()
        context = await authenticate_user_session(session, raw_token)
        is_admin = bool(context and context.subject.is_admin)
        await session.rollback()
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_admin_token",
        )


def _session_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer sess-"):
        return auth[7:].strip()
    return None


async def _load_vllm_metric_targets() -> list[VLLMMetricsTarget]:
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
        await session.rollback()
    return [
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
