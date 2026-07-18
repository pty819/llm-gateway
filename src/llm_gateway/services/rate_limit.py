from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import asyncio
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
# TTL on the concurrency ZSET: a hard cap on how long a leaked slot can block
# new requests after a client disconnect / process crash / cancellation.
# Chosen to comfortably exceed the longest expected single upstream call
# (upstream_timeout_seconds defaults to 6000s = 100min) while still giving
# operators a deterministic recovery window. Must be longer than
# upstream_timeout_seconds so a genuinely slow request never has its slot
# pruned mid-flight.
CONCURRENCY_SLOT_TTL_SECONDS = 60 * 60 * 2  # 2 hours
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
# Atomic release so cleanup after a cancelled/disconnected request cannot be
# split across round-trips — Lua runs server-side, immune to client-side
# CancelledError propagation.
RELEASE_CONCURRENCY_SLOT_SCRIPT = """
local key = KEYS[1]
local member = ARGV[1]
local removed = redis.call("ZREM", key, member)
redis.call("EXPIRE", key, tonumber(ARGV[2]))
return removed
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
    redis: Redis | None = None,
) -> EffectiveRatePolicy:
    from llm_gateway.services import subject_rate_override
    from llm_gateway.services.cache import policy_cache

    # Per-subject Redis override: absolute highest priority. If a dimension is
    # set here, it short-circuits the entire min() resolution for that
    # dimension — the admin's explicit per-user limit wins over every other
    # source (env defaults, key/project/subject RatePolicies). Read live from
    # Redis every request (single GET, sub-millisecond) so an admin's edit
    # takes effect immediately with no cache lag. Degrades open: Redis down →
    # fall through to the normal PG-based path.
    override = await subject_rate_override.get_override(redis, subject_id)

    # The PG-based resolution result is still cached per (key, subject, project)
    # for 30s. But when a Redis override is present we bypass that cache: the
    # override is the live source of truth and must not be masked by a stale
    # cached PG result. When no override exists, the cache is safe to use.
    cache_key = f"rate:{key_id}:{subject_id}:{project_id}"
    if override is None:
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

    # Apply the Redis override last so it wins absolutely for the dimensions it
    # covers. A None dimension leaves the PG-computed value in place.
    if override is not None:
        if override.concurrency is not None:
            concurrency_limit = override.concurrency
        if override.rpm is not None:
            requests_per_minute = override.rpm

    effective = EffectiveRatePolicy(
        requests_per_minute=requests_per_minute, concurrency_limit=concurrency_limit
    )
    # Only cache the PG-only result. A result that incorporates a live Redis
    # override must not be cached, or an admin edit would be masked for up to
    # the cache TTL.
    if override is None:
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
    ttl_seconds: int = CONCURRENCY_SLOT_TTL_SECONDS,
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
    try:
        # Use a server-side Lua script so release is atomic: a single Eval call
        # cannot be split by client-side CancelledError the way separate zrem
        # + expire could. Even if the calling coroutine is cancelled while the
        # Eval is in flight, Redis already executed it — the slot is removed.
        if hasattr(redis, "eval"):
            eval_result = redis.eval(
                RELEASE_CONCURRENCY_SLOT_SCRIPT,
                1,
                slot_key,
                member,
                str(CONCURRENCY_SLOT_TTL_SECONDS),
            )
            if inspect.isawaitable(eval_result):
                await asyncio.shield(eval_result)
        else:
            await asyncio.shield(redis.zrem(slot_key, member))
    except (RedisError, asyncio.CancelledError):
        # Best-effort release: if Redis is gone or the release itself is being
        # cancelled, the TTL on the ZSET (CONCURRENCY_SLOT_TTL_SECONDS) is the
        # backstop — the stale member will be pruned automatically. Never let
        # a release failure propagate as a caller-visible error.
        pass


@asynccontextmanager
async def concurrency_slot(
    redis: Redis,
    *,
    key_id: UUID,
    limit: int,
    ttl_seconds: int = CONCURRENCY_SLOT_TTL_SECONDS,
    now: float | None = None,
) -> AsyncGenerator[None, None]:
    slot_token = await acquire_concurrency_slot(
        redis, key_id=key_id, limit=limit, ttl_seconds=ttl_seconds, now=now
    )
    try:
        yield
    finally:
        # Shield release so client disconnect / task cancellation cannot
        # prevent the slot from being returned. The release is a single Lua
        # Eval (atomic server-side), so even if shield is partially cancelled
        # the Redis command still completes.
        await asyncio.shield(release_concurrency_slot(redis, slot_token))


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
