from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.core.config import Settings, get_settings
from llm_gateway.db.models import RatePolicy, ResourceState


def create_redis(settings: Settings | None = None) -> Redis:
    resolved = settings or get_settings()
    return Redis.from_url(resolved.redis_url, decode_responses=True)


redis_client = create_redis()


class RateLimitExceeded(Exception):
    pass


class EffectiveRatePolicy:
    def __init__(self, requests_per_minute: int, concurrency_limit: int):
        self.requests_per_minute = requests_per_minute
        self.concurrency_limit = concurrency_limit


async def resolve_effective_rate_policy(
    session: AsyncSession,
    *,
    key_id: UUID,
    subject_id: UUID,
    project_id: UUID,
    defaults: Settings,
) -> EffectiveRatePolicy:
    from llm_gateway.services.cache import policy_cache

    cache_key = f"rate:{key_id}:{subject_id}:{project_id}"
    cached = policy_cache.get(cache_key)
    if cached is not None:
        return cached
    requests_per_minute = defaults.default_request_limit_per_minute
    concurrency_limit = defaults.default_concurrency_limit
    for scope, scope_id in (
        ("key", key_id),
        ("subject", subject_id),
        ("project", project_id),
    ):
        result = await session.execute(
            select(RatePolicy).where(
                col(RatePolicy.scope) == scope,
                col(RatePolicy.scope_id) == scope_id,
                col(RatePolicy.state) == ResourceState.ACTIVE,
            )
        )
        for policy in result.scalars().all():
            if policy.requests_per_minute is not None:
                requests_per_minute = min(
                    requests_per_minute, policy.requests_per_minute
                )
            if policy.concurrency_limit is not None:
                concurrency_limit = min(concurrency_limit, policy.concurrency_limit)
    effective = EffectiveRatePolicy(
        requests_per_minute=requests_per_minute, concurrency_limit=concurrency_limit
    )
    policy_cache.set(cache_key, effective)
    return effective


async def check_request_rate(
    redis: Redis,
    *,
    key_id: UUID,
    limit: int,
    window_seconds: int = 60,
) -> None:
    counter_key = f"rate:key:{key_id}:{window_seconds}"
    current = await redis.incr(counter_key)
    if current == 1:
        await redis.expire(counter_key, window_seconds)
    if current > limit:
        raise RateLimitExceeded("request_rate_exceeded")


async def acquire_concurrency_slot(
    redis: Redis,
    *,
    key_id: UUID,
    limit: int,
    ttl_seconds: int = 900,
) -> str:
    counter_key = f"concurrency:key:{key_id}"
    current = await redis.incr(counter_key)
    await redis.expire(counter_key, ttl_seconds)
    if current > limit:
        await redis.decr(counter_key)
        raise RateLimitExceeded("concurrency_exceeded")
    return counter_key


async def release_concurrency_slot(redis: Redis, counter_key: str) -> None:
    await redis.decr(counter_key)


@asynccontextmanager
async def concurrency_slot(
    redis: Redis,
    *,
    key_id: UUID,
    limit: int,
    ttl_seconds: int = 900,
) -> AsyncGenerator[None, None]:
    counter_key = await acquire_concurrency_slot(
        redis, key_id=key_id, limit=limit, ttl_seconds=ttl_seconds
    )
    try:
        yield
    finally:
        await release_concurrency_slot(redis, counter_key)
