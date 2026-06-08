from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from llm_gateway.db.models import ModelAlias, UpstreamTarget
from llm_gateway.services.runtime_metrics import (
    ACTIVE_KEY,
    ACTIVE_STALE_SECONDS,
    VLLM_METRICS_CACHE_PREFIX,
)


STICKY_ROUTE_PREFIX = "llm_gateway:routing:sticky"


@dataclass(frozen=True)
class UpstreamLoad:
    upstream_id: str
    active_connections: int
    kv_cache_usage: float | None
    score: float
    sticky: bool = False


def sticky_route_key(*, key_id: UUID, model_alias_id: UUID) -> str:
    return f"{STICKY_ROUTE_PREFIX}:{key_id}:{model_alias_id}"


async def select_upstream_for_key(
    redis: Redis | None,
    *,
    key_id: UUID,
    model_alias: ModelAlias,
    upstreams: list[UpstreamTarget],
    now: float | None = None,
) -> tuple[UpstreamTarget, list[UpstreamLoad]]:
    if not upstreams:
        raise ValueError("upstream_not_configured")

    now = now if now is not None else time.time()
    sticky_ttl = max(int(model_alias.sticky_ttl_seconds or 1200), 1)
    upstream_by_id = {str(upstream.id): upstream for upstream in upstreams}

    if redis is None:
        selected = _stable_fallback_choice(upstreams, key=f"{key_id}:{model_alias.id}")
        return selected, _empty_loads(upstreams, selected_id=str(selected.id))

    loads = await load_upstream_loads(redis, upstreams=upstreams, now=now)
    sticky_upstream_id = await _read_sticky_upstream_id(
        redis, key_id=key_id, model_alias_id=model_alias.id
    )
    if sticky_upstream_id in upstream_by_id:
        selected = upstream_by_id[sticky_upstream_id]
    else:
        selected = _lowest_load_choice(
            upstreams,
            loads=loads,
            tie_break_key=f"{key_id}:{model_alias.id}",
        )
    await touch_sticky_route(
        redis,
        key_id=key_id,
        model_alias_id=model_alias.id,
        upstream_id=selected.id,
        ttl_seconds=sticky_ttl,
        now=now,
    )
    return selected, [
        UpstreamLoad(
            upstream_id=load.upstream_id,
            active_connections=load.active_connections,
            kv_cache_usage=load.kv_cache_usage,
            score=load.score,
            sticky=load.upstream_id == str(selected.id),
        )
        for load in loads
    ]


async def touch_sticky_route(
    redis: Redis,
    *,
    key_id: UUID,
    model_alias_id: UUID,
    upstream_id: UUID,
    ttl_seconds: int,
    now: float | None = None,
) -> None:
    now = now if now is not None else time.time()
    payload = {
        "upstream_id": str(upstream_id),
        "last_active_at": now,
    }
    await redis.setex(
        sticky_route_key(key_id=key_id, model_alias_id=model_alias_id),
        max(int(ttl_seconds), 1),
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


async def load_upstream_loads(
    redis: Redis,
    *,
    upstreams: list[UpstreamTarget],
    now: float | None = None,
) -> list[UpstreamLoad]:
    now = now if now is not None else time.time()
    upstream_ids = [str(upstream.id) for upstream in upstreams]
    counts = await _active_connection_counts(redis, upstream_ids=upstream_ids, now=now)
    metric_values = await redis.mget(
        [f"{VLLM_METRICS_CACHE_PREFIX}:{upstream_id}" for upstream_id in upstream_ids]
    )
    loads: list[UpstreamLoad] = []
    for upstream_id, raw_metrics in zip(upstream_ids, metric_values, strict=True):
        kv_cache_usage = _kv_cache_usage(raw_metrics)
        active_connections = counts.get(upstream_id, 0)
        pressure = kv_cache_usage if kv_cache_usage is not None else 1.0
        loads.append(
            UpstreamLoad(
                upstream_id=upstream_id,
                active_connections=active_connections,
                kv_cache_usage=kv_cache_usage,
                score=pressure * (active_connections + 1),
            )
        )
    return loads


async def _read_sticky_upstream_id(
    redis: Redis, *, key_id: UUID, model_alias_id: UUID
) -> str | None:
    raw = await redis.get(
        sticky_route_key(key_id=key_id, model_alias_id=model_alias_id)
    )
    if not raw:
        return None
    try:
        payload = json.loads(_decode_value(raw))
        upstream_id = payload.get("upstream_id")
    except TypeError, ValueError, AttributeError:
        return None
    return upstream_id if isinstance(upstream_id, str) else None


async def _active_connection_counts(
    redis: Redis, *, upstream_ids: list[str], now: float
) -> dict[str, int]:
    await redis.zremrangebyscore(ACTIVE_KEY, "-inf", now - ACTIVE_STALE_SECONDS)
    wanted = set(upstream_ids)
    counts = {upstream_id: 0 for upstream_id in upstream_ids}
    for item in await redis.zrange(ACTIVE_KEY, 0, -1):
        try:
            payload = json.loads(_decode_value(item))
        except TypeError, ValueError:
            continue
        upstream_id = payload.get("upstream_id")
        if upstream_id in wanted:
            counts[upstream_id] += 1
    return counts


def _kv_cache_usage(raw_metrics: Any) -> float | None:
    if not raw_metrics:
        return None
    try:
        payload = json.loads(_decode_value(raw_metrics))
    except TypeError, ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("ignore"):
        return None
    value = payload.get("kv_cache_usage")
    return float(value) if isinstance(value, int | float) else None


def _lowest_load_choice(
    upstreams: list[UpstreamTarget], *, loads: list[UpstreamLoad], tie_break_key: str
) -> UpstreamTarget:
    load_by_id = {load.upstream_id: load for load in loads}
    return min(
        upstreams,
        key=lambda upstream: (
            load_by_id.get(str(upstream.id), _fallback_load(str(upstream.id))).score,
            _stable_hash(f"{tie_break_key}:{upstream.id}"),
        ),
    )


def _stable_fallback_choice(
    upstreams: list[UpstreamTarget], *, key: str
) -> UpstreamTarget:
    return min(upstreams, key=lambda upstream: _stable_hash(f"{key}:{upstream.id}"))


def _empty_loads(
    upstreams: list[UpstreamTarget], *, selected_id: str
) -> list[UpstreamLoad]:
    return [
        UpstreamLoad(
            upstream_id=str(upstream.id),
            active_connections=0,
            kv_cache_usage=None,
            score=1.0,
            sticky=str(upstream.id) == selected_id,
        )
        for upstream in upstreams
    ]


def _fallback_load(upstream_id: str) -> UpstreamLoad:
    return UpstreamLoad(
        upstream_id=upstream_id,
        active_connections=0,
        kv_cache_usage=None,
        score=1.0,
    )


def _stable_hash(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big")


def _decode_value(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
