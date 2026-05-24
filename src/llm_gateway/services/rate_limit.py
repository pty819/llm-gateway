from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from redis.asyncio import Redis

from llm_gateway.core.config import Settings, get_settings


def create_redis(settings: Settings | None = None) -> Redis:
    resolved = settings or get_settings()
    return Redis.from_url(resolved.redis_url, decode_responses=True)


redis_client = create_redis()


class RateLimitExceeded(Exception):
    pass


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


@asynccontextmanager
async def concurrency_slot(
    redis: Redis,
    *,
    key_id: UUID,
    limit: int,
    ttl_seconds: int = 900,
) -> AsyncGenerator[None, None]:
    counter_key = f"concurrency:key:{key_id}"
    current = await redis.incr(counter_key)
    await redis.expire(counter_key, ttl_seconds)
    if current > limit:
        await redis.decr(counter_key)
        raise RateLimitExceeded("concurrency_exceeded")
    try:
        yield
    finally:
        await redis.decr(counter_key)

