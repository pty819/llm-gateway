"""Integration + unit tests for the streaming concurrency-slot lifecycle.

These cover the two slot-leak scenarios fixed by the streaming-slot-leak work:

1. **Construction window** (lazy acquire): the concurrency slot is now acquired
   *inside* ``_stream_endpoint`` (the generator) rather than in the handler
   before ``StreamingResponse`` is built. If the client disconnects right after
   the response is constructed, the generator simply never runs, so no slot is
   ever held. When concurrency is exhausted, the generator emits an SSE error
   frame instead of the handler raising a 429 it can no longer send.

2. **Ungraceful disconnect** (watchdog): ``iter_with_heartbeat`` polls
   ``request.is_disconnected`` and cancels the producer on disconnect, which
   rides the existing ``CancelledError`` → ``finally`` → ``release_concurrency_slot``
   chain so the slot is freed within ~2× the watchdog interval.

The disconnect path is exercised as a pure unit test (``FakeRedis`` + mock
``Request``) so it runs everywhere; the end-to-end paths use the real
``client`` + ``gateway_fixture`` stack and require both the test DB and a
reachable Redis (they skip cleanly otherwise).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast
from uuid import uuid4

import pytest
import pytest_asyncio

from llm_gateway.api.proxy import _stream_endpoint
from llm_gateway.db.models import RequestOutcome
from llm_gateway.services.rate_limit import _concurrency_slot_key

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async redis stub covering the ops the streaming path touches:
    concurrency slot ZSET (zadd/zrem/zcard/zremrangebyscore/expire) and the
    runtime-metrics active-connection ZSET (same ops). Mirrors the FakeRedis in
    tests/test_rate_limit.py.
    """

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, name: str) -> int:
        self.values[name] = self.values.get(name, 0) + 1
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


class _MockSubject:
    def __init__(self) -> None:
        self.id = uuid4()
        self.type = "user"


class _MockProject:
    def __init__(self) -> None:
        self.id = uuid4()


class _MockKey:
    def __init__(self) -> None:
        self.id = uuid4()


class _MockAuth:
    def __init__(self) -> None:
        self.subject = _MockSubject()
        self.project = _MockProject()
        self.key = _MockKey()


class _MockModelAlias:
    def __init__(self) -> None:
        self.id = uuid4()
        self.alias = "pytest-model"
        self.sticky_ttl_seconds = 0


class _MockUpstream:
    def __init__(self) -> None:
        self.id = uuid4()
        self.name = "pytest-upstream"


class _MockRoute:
    def __init__(self) -> None:
        self.model_alias = _MockModelAlias()
        self.upstream = _MockUpstream()


class _MockRatePolicy:
    def __init__(self, concurrency_limit: int = 8) -> None:
        self.concurrency_limit = concurrency_limit


class _MockRequest:
    """Starlette-like Request whose ``is_disconnected`` flips True after a
    flag is set, simulating an ungraceful client disconnect mid-stream."""

    def __init__(self) -> None:
        self._disconnected = False

    def mark_disconnected(self) -> None:
        self._disconnected = True

    async def is_disconnected(self) -> bool:
        return self._disconnected


# ---------------------------------------------------------------------------
# Unit test: slot released on ungraceful disconnect (the core fix)
#
# This is the highest-value test and runs without a DB or real Redis. It drives
# ``_stream_endpoint`` directly with a FakeRedis and a mock Request whose
# ``is_disconnected`` flips after the first event, then asserts:
#   - CancelledError propagates to the consumer (existing accounting path),
#   - the slot is released within ~2× the watchdog interval,
#   - the recorded outcome is CLIENT_CANCELLED.
# ---------------------------------------------------------------------------


async def test_streaming_slot_released_on_client_disconnect(monkeypatch):
    # Avoid touching the DB / real redis from the accounting + sticky-route
    # helpers; we only care about the concurrency-slot ZSET on the FakeRedis.
    recorded: dict[str, Any] = {}

    async def fake_record_proxy_fact(**kwargs):
        recorded.update(kwargs)

    async def fake_touch_sticky(redis, *, auth, route):
        return None

    monkeypatch.setattr("llm_gateway.api.proxy.record_proxy_fact", fake_record_proxy_fact)
    monkeypatch.setattr("llm_gateway.api.proxy._touch_route_sticky", fake_touch_sticky)

    redis = cast(Any, _FakeRedis())
    auth = _MockAuth()
    route = _MockRoute()
    rate_policy = _MockRatePolicy(concurrency_limit=1)
    mock_request = _MockRequest()

    # Fake upstream: yield one event, then go silent (simulating a stuck
    # generator after the client has already gone away).
    async def fake_upstream_stream(*, endpoint_family, model_alias, upstream, body):
        yield ("data: first\n\n", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        # Flip the disconnect flag shortly after the first token so the watchdog
        # (interval=0.02s) observes it on its next poll.
        asyncio.get_event_loop().call_later(0.05, mock_request.mark_disconnected)
        await asyncio.sleep(10.0)  # long silence; watchdog must break this
        yield ("data: never\n\n", None)  # pragma: no cover — should never reach

    monkeypatch.setattr("llm_gateway.api.proxy.upstream_request_stream", fake_upstream_stream)

    slot_key = _concurrency_slot_key(auth.key.id)

    start = time.monotonic()
    seen: list[str] = []
    with pytest.raises(asyncio.CancelledError):
        async for event in _stream_endpoint(
            endpoint_family="openai_chat",
            stream_endpoint="stream_openai",
            redis=redis,
            auth=auth,
            route=route,
            rate_policy=rate_policy,
            body={"model": "pytest-model", "stream": True},
            started_at=asyncio.get_event_loop().time(),
            request_id=f"pytest-disc-{uuid4()}",
            keepalive_seconds=1.0,
            request=mock_request,
            watchdog_interval=0.02,
        ):
            seen.append(event)
    elapsed = time.monotonic() - start

    # The first (and only) real event was forwarded before the disconnect.
    assert seen == ["data: first\n\n"]
    # Watchdog broke the silence promptly: well under the 10s sleep, within
    # ~2× the 0.02s interval.
    assert elapsed < 1.0, f"slot released too slowly: {elapsed:.3f}s"
    # The slot was released in the finally block.
    assert await redis.zcard(slot_key) == 0
    # Outcome recorded as a client cancellation.
    assert recorded.get("outcome") == RequestOutcome.CLIENT_CANCELLED


# ---------------------------------------------------------------------------
# Unit test: lazy acquire emits an SSE error frame when concurrency is exhausted
#
# With the old eager-acquire path, an exhausted slot raised a 429 from the
# handler. Now the StreamingResponse has already started (200 sent), so the
# generator must emit an SSE error frame and must NOT hold a slot.
# ---------------------------------------------------------------------------


async def test_streaming_lazy_acquire_concurrency_exceeded(monkeypatch):
    async def fake_record_proxy_fact(**kwargs):
        pass

    async def fake_touch_sticky(redis, *, auth, route):
        return None

    monkeypatch.setattr("llm_gateway.api.proxy.record_proxy_fact", fake_record_proxy_fact)
    monkeypatch.setattr("llm_gateway.api.proxy._touch_route_sticky", fake_touch_sticky)

    redis = cast(Any, _FakeRedis())
    auth = _MockAuth()
    route = _MockRoute()
    rate_policy = _MockRatePolicy(concurrency_limit=1)
    mock_request = _MockRequest()
    slot_key = _concurrency_slot_key(auth.key.id)

    # Pre-fill the single allowed slot so the next acquire is rejected. Use
    # the real current time (not a fake epoch) so the lazy acquire inside
    # _stream_endpoint — which prunes slots older than now - ttl — does not
    # treat the pre-filled entry as stale and prune it before counting.
    from llm_gateway.services.rate_limit import acquire_concurrency_slot

    await acquire_concurrency_slot(
        redis, key_id=auth.key.id, limit=1, ttl_seconds=900, now=time.time()
    )
    assert await redis.zcard(slot_key) == 1
    slots_before = await redis.zcard(slot_key)

    upstream_called = False

    async def fake_upstream_stream(*, endpoint_family, model_alias, upstream, body):
        nonlocal upstream_called
        upstream_called = True
        yield ("data: should-not-happen\n\n", None)  # pragma: no cover

    monkeypatch.setattr("llm_gateway.api.proxy.upstream_request_stream", fake_upstream_stream)

    frames: list[str] = []
    async for event in _stream_endpoint(
        endpoint_family="openai_chat",
        stream_endpoint="stream_openai",
        redis=redis,
        auth=auth,
        route=route,
        rate_policy=rate_policy,
        body={"model": "pytest-model", "stream": True},
        started_at=asyncio.get_event_loop().time(),
        request_id=f"pytest-exhausted-{uuid4()}",
        keepalive_seconds=1.0,
        request=mock_request,
        watchdog_interval=0.02,
    ):
        frames.append(event)

    # An SSE error frame was emitted (not a silent hang / not a 429).
    assert len(frames) == 1
    assert "event: error" in frames[0]
    assert "concurrency_exceeded" in frames[0]
    # The upstream was never contacted.
    assert upstream_called is False
    # No new slot was added (ZCARD unchanged).
    assert await redis.zcard(slot_key) == slots_before


# ---------------------------------------------------------------------------
# Unit test: watchdog disabled by a zero interval still streams + releases
# ---------------------------------------------------------------------------


async def test_streaming_watchdog_disabled_by_zero_interval(monkeypatch):
    async def fake_record_proxy_fact(**kwargs):
        pass

    async def fake_touch_sticky(redis, *, auth, route):
        return None

    monkeypatch.setattr("llm_gateway.api.proxy.record_proxy_fact", fake_record_proxy_fact)
    monkeypatch.setattr("llm_gateway.api.proxy._touch_route_sticky", fake_touch_sticky)

    redis = cast(Any, _FakeRedis())
    auth = _MockAuth()
    route = _MockRoute()
    rate_policy = _MockRatePolicy(concurrency_limit=8)
    mock_request = _MockRequest()

    async def fake_upstream_stream(*, endpoint_family, model_alias, upstream, body):
        yield ("data: a\n\n", None)
        yield ("data: b\n\n", None)

    monkeypatch.setattr("llm_gateway.api.proxy.upstream_request_stream", fake_upstream_stream)

    slot_key = _concurrency_slot_key(auth.key.id)
    frames: list[str] = []
    async for event in _stream_endpoint(
        endpoint_family="openai_chat",
        stream_endpoint="stream_openai",
        redis=redis,
        auth=auth,
        route=route,
        rate_policy=rate_policy,
        body={"model": "pytest-model", "stream": True},
        started_at=asyncio.get_event_loop().time(),
        request_id=f"pytest-no-watchdog-{uuid4()}",
        keepalive_seconds=1.0,
        request=mock_request,
        watchdog_interval=0.0,  # disabled
    ):
        frames.append(event)

    assert frames == ["data: a\n\n", "data: b\n\n"]
    # Slot released on normal completion.
    assert await redis.zcard(slot_key) == 0


# ---------------------------------------------------------------------------
# End-to-end regression tests (require test DB + reachable Redis)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _require_redis():
    """Skip the test cleanly when Redis is not reachable, rather than erroring
    through the rate-limit / concurrency / metrics code paths."""
    from redis.asyncio import Redis

    from llm_gateway.core.config import get_settings

    r = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        reachable = bool(await r.ping())
    except Exception:
        reachable = False
    finally:
        await r.aclose()

    if not reachable:
        pytest.skip("Redis is not reachable; streaming integration tests need it.")


async def test_streaming_slot_released_on_normal_completion(
    client, gateway_fixture, monkeypatch, _require_redis
):
    """Regression: a stream that completes normally must release its slot.

    Monkeypatches ``upstream_request_stream`` so no real upstream is hit; the
    full proxy stack (auth, route resolution, redis concurrency, fact
    recording) is otherwise exercised.
    """
    from llm_gateway.services.rate_limit import _concurrency_slot_key

    async def fake_upstream_stream(*, endpoint_family, model_alias, upstream, body):
        yield ("data: hello\n\n", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        yield ("data: [DONE]\n\n", None)

    monkeypatch.setattr("llm_gateway.api.proxy.upstream_request_stream", fake_upstream_stream)

    request_id = f"pytest-stream-slot-release-{uuid4()}"
    slot_key = _concurrency_slot_key(gateway_fixture.key_id)

    # Clean any leftover slot key from prior runs against the shared redis.
    from llm_gateway.services.rate_limit import redis_client

    await redis_client.delete(slot_key)

    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {gateway_fixture.raw_key}",
            "x-request-id": request_id,
        },
        json={
            "model": gateway_fixture.model_alias,
            "messages": [{"role": "user", "content": "stream"}],
            "max_tokens": 16,
            "stream": True,
        },
    ) as response:
        body = await response.aread()

    assert response.status_code == 200
    assert b"data:" in body

    # Give the async fact-queue a moment to drain, then assert the slot is free.
    await asyncio.sleep(0.1)
    assert await redis_client.zcard(slot_key) == 0

    from conftest import fetch_request_fact

    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.SUCCESS
    assert fact.streaming is True
