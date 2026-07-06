from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from llm_gateway.services.rate_limit import (
    RateLimitExceeded,
    acquire_concurrency_slot,
    concurrency_slot,
    release_concurrency_slot,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, name: str) -> int:
        self.values[name] = self.values.get(name, 0) + 1
        return self.values[name]

    async def decr(self, name: str) -> int:
        self.values[name] = self.values.get(name, 0) - 1
        return self.values[name]

    async def expire(self, name: str, seconds: int) -> None:
        self.expirations[name] = seconds

    async def zadd(self, name: str, mapping: dict[str, float]) -> None:
        self.zsets.setdefault(name, {}).update(mapping)

    async def zrem(self, name: str, member: str) -> None:
        self.zsets.setdefault(name, {}).pop(member, None)

    async def zremrangebyscore(self, name: str, minimum: Any, maximum: Any) -> None:
        del minimum
        max_score = float(maximum)
        zset = self.zsets.setdefault(name, {})
        for member, score in list(zset.items()):
            if score <= max_score:
                zset.pop(member, None)

    async def zcard(self, name: str) -> int:
        return len(self.zsets.setdefault(name, {}))


async def test_concurrency_slots_prune_stale_entries_after_gateway_crash():
    redis = cast(Any, FakeRedis())
    key_id = uuid4()

    await acquire_concurrency_slot(redis, key_id=key_id, limit=1, ttl_seconds=10, now=100.0)

    with pytest.raises(RateLimitExceeded, match="concurrency_exceeded"):
        await acquire_concurrency_slot(redis, key_id=key_id, limit=1, ttl_seconds=10, now=105.0)

    await acquire_concurrency_slot(redis, key_id=key_id, limit=1, ttl_seconds=10, now=111.0)


async def test_releasing_concurrency_slot_is_idempotent():
    redis = cast(Any, FakeRedis())
    key_id = uuid4()

    slot = await acquire_concurrency_slot(redis, key_id=key_id, limit=1, ttl_seconds=10, now=100.0)

    await release_concurrency_slot(redis, slot)
    await release_concurrency_slot(redis, slot)
    await acquire_concurrency_slot(redis, key_id=key_id, limit=1, ttl_seconds=10, now=101.0)


async def test_concurrency_slot_context_releases_only_its_own_member():
    redis = cast(Any, FakeRedis())
    key_id = uuid4()

    async with concurrency_slot(redis, key_id=key_id, limit=2, ttl_seconds=10, now=100.0):
        second = await acquire_concurrency_slot(
            redis, key_id=key_id, limit=2, ttl_seconds=10, now=101.0
        )

    with pytest.raises(RateLimitExceeded, match="concurrency_exceeded"):
        await acquire_concurrency_slot(redis, key_id=key_id, limit=1, ttl_seconds=10, now=102.0)

    await release_concurrency_slot(redis, second)
    await acquire_concurrency_slot(redis, key_id=key_id, limit=1, ttl_seconds=10, now=103.0)
