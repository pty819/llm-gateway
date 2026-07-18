"""Tests for the per-subject Redis rate-limit override.

The override is the absolute-highest-priority rate-limit source: when set for
a dimension (RPM or concurrency), it short-circuits the normal min() across PG
RatePolicies + defaults. These tests cover the Redis service layer and the
resolver integration without a real Redis or PG — a FakeRedis + a stub
AsyncSession mirror the patterns in test_rate_limit.py.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from llm_gateway.services import subject_rate_override
from llm_gateway.services.rate_limit import EffectiveRatePolicy, resolve_effective_rate_policy
from llm_gateway.services.subject_rate_override import SubjectRateOverride


pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeResult:
    def __init__(self, rows: list[Any] = ()) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _StubSession:
    """AsyncSession stub whose execute() always returns an empty policy list.

    The resolver's PG path runs min() across an empty policy set, so the only
    limits in play are the env defaults — unless an override wins. This lets
    us isolate the override's precedence without spinning up PG.
    """

    async def execute(self, _stmt):
        return _FakeResult([])


class FakeRedis:
    """Minimal Redis stub covering GET/MGET/SET/DELETE/SCAN.

    Stores string values keyed by the full Redis key, mirroring decode_responses=True.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]

    async def set(self, key: str, value: str) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def scan(self, *, cursor: int, match: str, count: int):
        del count
        prefix = match.rstrip("*")
        matched = [k for k in self.store if k.startswith(prefix)]
        # Single-batch scan: return everything in one go with cursor 0.
        return 0, matched


class _Defaults:
    """Stand-in for Settings with only the two fields the resolver reads."""

    def __init__(self, rpm: int, concurrency: int) -> None:
        self.default_request_limit_per_minute = rpm
        self.default_concurrency_limit = concurrency


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


async def test_get_override_returns_none_when_unset():
    redis = cast(Any, FakeRedis())
    assert await subject_rate_override.get_override(redis, uuid4()) is None


async def test_get_override_returns_none_when_redis_is_none():
    assert await subject_rate_override.get_override(None, uuid4()) is None


async def test_set_then_get_round_trip():
    redis = cast(Any, FakeRedis())
    subject_id = uuid4()
    await subject_rate_override.set_override(
        redis, subject_id, rpm=100, concurrency=20
    )
    override = await subject_rate_override.get_override(redis, subject_id)
    assert override == SubjectRateOverride(rpm=100, concurrency=20)


async def test_set_with_both_none_clears_key():
    redis = cast(Any, FakeRedis())
    subject_id = uuid4()
    await subject_rate_override.set_override(
        redis, subject_id, rpm=50, concurrency=10
    )
    # Now clear it by setting both to None (PUT {null, null}).
    await subject_rate_override.set_override(redis, subject_id, rpm=None, concurrency=None)
    assert await subject_rate_override.get_override(redis, subject_id) is None
    # And the Redis key itself is gone (not left as an empty JSON blob).
    assert all("subject_override" not in k for k in redis.store)


async def test_clear_override_is_idempotent():
    redis = cast(Any, FakeRedis())
    subject_id = uuid4()
    await subject_rate_override.clear_override(redis, subject_id)  # no-op
    await subject_rate_override.set_override(redis, subject_id, rpm=10, concurrency=5)
    await subject_rate_override.clear_override(redis, subject_id)
    await subject_rate_override.clear_override(redis, subject_id)  # second clear
    assert await subject_rate_override.get_override(redis, subject_id) is None


async def test_non_positive_values_coerced_to_none():
    """A 0/negative limit must never lock a user out — coerce to None."""
    redis = cast(Any, FakeRedis())
    subject_id = uuid4()
    result = await subject_rate_override.set_override(
        redis, subject_id, rpm=0, concurrency=-5
    )
    assert result == SubjectRateOverride(rpm=None, concurrency=None)
    assert await subject_rate_override.get_override(redis, subject_id) is None


async def test_list_overrides_returns_only_non_empty():
    redis = cast(Any, FakeRedis())
    sid_a, sid_b, sid_c = uuid4(), uuid4(), uuid4()
    await subject_rate_override.set_override(redis, sid_a, rpm=10, concurrency=5)
    await subject_rate_override.set_override(redis, sid_b, rpm=None, concurrency=7)
    # sid_c gets an empty override → effectively cleared, must not appear
    await subject_rate_override.set_override(redis, sid_c, rpm=None, concurrency=None)

    out = await subject_rate_override.list_overrides(redis, [sid_a, sid_b, sid_c])
    assert set(out.keys()) == {str(sid_a), str(sid_b)}
    assert out[str(sid_a)].concurrency == 5
    assert out[str(sid_b)].rpm is None
    assert out[str(sid_b)].concurrency == 7


async def test_list_overrides_empty_input_returns_empty():
    redis = cast(Any, FakeRedis())
    assert await subject_rate_override.list_overrides(redis, []) == {}
    assert await subject_rate_override.list_overrides(None, [uuid4()]) == {}


async def test_partial_override_only_concurrency():
    """The common case: override only concurrency, leave RPM on default path."""
    redis = cast(Any, FakeRedis())
    subject_id = uuid4()
    await subject_rate_override.set_override(
        redis, subject_id, rpm=None, concurrency=30
    )
    override = await subject_rate_override.get_override(redis, subject_id)
    assert override is not None
    assert override.rpm is None
    assert override.concurrency == 30


# ---------------------------------------------------------------------------
# Resolver integration tests
# ---------------------------------------------------------------------------


async def test_resolver_uses_override_concurrency_ignoring_defaults():
    """Override wins absolutely for the dimensions it covers."""
    # Purge the in-process policy cache so it can't mask the test.
    from llm_gateway.services.cache import policy_cache
    policy_cache.invalidate()

    redis = cast(Any, FakeRedis())
    subject_id, key_id, project_id = uuid4(), uuid4(), uuid4()
    defaults = _Defaults(rpm=120, concurrency=8)

    await subject_rate_override.set_override(
        redis, subject_id, rpm=None, concurrency=50
    )
    policy = await resolve_effective_rate_policy(
        cast(Any, _StubSession()),
        key_id=key_id,
        subject_id=subject_id,
        project_id=project_id,
        defaults=cast(Any, defaults),
        redis=redis,
    )
    assert policy.concurrency_limit == 50  # override wins
    assert policy.requests_per_minute == 120  # RPM falls back to default


async def test_resolver_override_both_dimensions():
    from llm_gateway.services.cache import policy_cache
    policy_cache.invalidate()

    redis = cast(Any, FakeRedis())
    subject_id, key_id, project_id = uuid4(), uuid4(), uuid4()
    defaults = _Defaults(rpm=120, concurrency=8)

    await subject_rate_override.set_override(
        redis, subject_id, rpm=200, concurrency=40
    )
    policy = await resolve_effective_rate_policy(
        cast(Any, _StubSession()),
        key_id=key_id,
        subject_id=subject_id,
        project_id=project_id,
        defaults=cast(Any, defaults),
        redis=redis,
    )
    assert policy.requests_per_minute == 200
    assert policy.concurrency_limit == 40


async def test_resolver_falls_back_to_defaults_when_no_override():
    from llm_gateway.services.cache import policy_cache
    policy_cache.invalidate()

    redis = cast(Any, FakeRedis())
    subject_id, key_id, project_id = uuid4(), uuid4(), uuid4()
    defaults = _Defaults(rpm=120, concurrency=8)

    policy = await resolve_effective_rate_policy(
        cast(Any, _StubSession()),
        key_id=key_id,
        subject_id=subject_id,
        project_id=project_id,
        defaults=cast(Any, defaults),
        redis=redis,
    )
    assert policy.requests_per_minute == 120
    assert policy.concurrency_limit == 8


async def test_resolver_degrades_open_when_redis_none():
    """No Redis → fall back to defaults, never raise."""
    from llm_gateway.services.cache import policy_cache
    policy_cache.invalidate()

    subject_id, key_id, project_id = uuid4(), uuid4(), uuid4()
    defaults = _Defaults(rpm=120, concurrency=8)

    policy = await resolve_effective_rate_policy(
        cast(Any, _StubSession()),
        key_id=key_id,
        subject_id=subject_id,
        project_id=project_id,
        defaults=cast(Any, defaults),
        redis=None,
    )
    assert policy.concurrency_limit == 8
    assert policy.requests_per_minute == 120


async def test_resolver_override_result_is_not_cached():
    """An override-influenced result must bypass the 30s policy cache so an
    admin edit applies to the very next request, not after the cache TTL."""
    from llm_gateway.services.cache import policy_cache
    policy_cache.invalidate()

    redis = cast(Any, FakeRedis())
    subject_id, key_id, project_id = uuid4(), uuid4(), uuid4()
    defaults = _Defaults(rpm=120, concurrency=8)

    # First call with override=20.
    await subject_rate_override.set_override(
        redis, subject_id, rpm=None, concurrency=20
    )
    p1 = await resolve_effective_rate_policy(
        cast(Any, _StubSession()),
        key_id=key_id,
        subject_id=subject_id,
        project_id=project_id,
        defaults=cast(Any, defaults),
        redis=redis,
    )
    assert p1.concurrency_limit == 20

    # Admin bumps the override to 40 — the next call must see 40 immediately,
    # proving the prior result was not cached.
    await subject_rate_override.set_override(
        redis, subject_id, rpm=None, concurrency=40
    )
    p2 = await resolve_effective_rate_policy(
        cast(Any, _StubSession()),
        key_id=key_id,
        subject_id=subject_id,
        project_id=project_id,
        defaults=cast(Any, defaults),
        redis=redis,
    )
    assert p2.concurrency_limit == 40
