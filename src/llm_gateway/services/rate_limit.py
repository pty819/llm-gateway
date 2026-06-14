from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import inspect
import time
from typing import Any
from uuid import UUID
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
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


CONCURRENCY_SLOT_PREFIX = "concurrency:key"
ACQUIRE_CONCURRENCY_SLOT_SCRIPT = """
local key = KEYS[1]
local member = ARGV[1]
local now = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local limit = tonumber(ARGV[4])
redis.call("ZREMRANGEBYSCORE", key, "-inf", now - ttl)
redis.call("ZADD", key, now, member)
local current = redis.call("ZCARD", key)
redis.call("EXPIRE", key, ttl)
if current > limit then
  redis.call("ZREM", key, member)
  return 0
end
return current
"""


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
    try:
        current = await redis.incr(counter_key)
        if current == 1:
            await redis.expire(counter_key, window_seconds)
    except RedisError:
        # Redis unavailable: honor the configured fail-closed policy instead of
        # relying on the exception to bubble up as a 500 by accident.
        if get_settings().rate_limit_fail_closed:
            raise RateLimitExceeded("rate_limit_unavailable")
        return
    if current > limit:
        raise RateLimitExceeded("request_rate_exceeded")


async def check_login_rate(
    redis: Redis,
    *,
    client_ip: str,
    limit: int = 20,
    window_seconds: int = 60,
) -> None:
    """Cap login/register attempts per source IP to blunt username enumeration
    and credential brute-force. Redis errors fail open: auth must not break if
    the rate-limit backend is unavailable."""
    if not client_ip:
        return
    counter_key = f"login:attempts:{client_ip}:{window_seconds}"
    try:
        current = await redis.incr(counter_key)
        if current == 1:
            await redis.expire(counter_key, window_seconds)
    except RedisError:
        return
    if current > limit:
        raise RateLimitExceeded("too_many_login_attempts")


async def acquire_concurrency_slot(
    redis: Redis,
    *,
    key_id: UUID,
    limit: int,
    ttl_seconds: int = 900,
    now: float | None = None,
) -> str:
    now = now if now is not None else time.time()
    slot_key = _concurrency_slot_key(key_id)
    member = f"{uuid4()}:{now:.6f}"
    try:
        acquired = await _try_acquire_slot(
            redis,
            slot_key=slot_key,
            member=member,
            ttl_seconds=ttl_seconds,
            limit=limit,
            now=now,
        )
    except RedisError:
        if get_settings().rate_limit_fail_closed:
            raise RateLimitExceeded("concurrency_unavailable")
        # Fail open: return an empty token whose release is a no-op so the
        # caller still gets a value to release later.
        return ""
    if not acquired:
        raise RateLimitExceeded("concurrency_exceeded")
    return _encode_slot_token(slot_key=slot_key, member=member)


async def release_concurrency_slot(redis: Redis, slot_token: str) -> None:
    parsed = _decode_slot_token(slot_token)
    if parsed is None:
        return
    slot_key, member = parsed
    await redis.zrem(slot_key, member)


@asynccontextmanager
async def concurrency_slot(
    redis: Redis,
    *,
    key_id: UUID,
    limit: int,
    ttl_seconds: int = 900,
    now: float | None = None,
) -> AsyncGenerator[None, None]:
    slot_token = await acquire_concurrency_slot(
        redis, key_id=key_id, limit=limit, ttl_seconds=ttl_seconds, now=now
    )
    try:
        yield
    finally:
        await release_concurrency_slot(redis, slot_token)


def _concurrency_slot_key(key_id: UUID) -> str:
    return f"{CONCURRENCY_SLOT_PREFIX}:{key_id}:slots"


async def _prune_stale_slots(
    redis: Redis, *, slot_key: str, ttl_seconds: int, now: float
) -> None:
    await redis.zremrangebyscore(slot_key, "-inf", now - ttl_seconds)


async def _try_acquire_slot(
    redis: Redis,
    *,
    slot_key: str,
    member: str,
    ttl_seconds: int,
    limit: int,
    now: float,
) -> bool:
    if hasattr(redis, "eval"):
        eval_result = redis.eval(
            ACQUIRE_CONCURRENCY_SLOT_SCRIPT,
            1,
            slot_key,
            member,
            str(now),
            str(ttl_seconds),
            str(limit),
        )
        result: Any = (
            await eval_result if inspect.isawaitable(eval_result) else eval_result
        )
        return int(result or 0) > 0

    await _prune_stale_slots(redis, slot_key=slot_key, ttl_seconds=ttl_seconds, now=now)
    await redis.zadd(slot_key, {member: now})
    current = await redis.zcard(slot_key)
    await redis.expire(slot_key, ttl_seconds)
    if current > limit:
        await redis.zrem(slot_key, member)
        return False
    return True


def _encode_slot_token(*, slot_key: str, member: str) -> str:
    return f"{slot_key}|{member}"


def _decode_slot_token(slot_token: str) -> tuple[str, str] | None:
    slot_key, separator, member = slot_token.partition("|")
    if not separator or not slot_key or not member:
        return None
    return slot_key, member
