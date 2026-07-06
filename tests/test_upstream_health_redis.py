"""Tests for the Redis-backed runtime liveness layer (upstream_health).

Verifies mark/clear/filter/is_unhealthy semantics, TTL behavior, JSON payload
shape, and graceful degradation when Redis is None or raises.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from llm_gateway.services import upstream_health


class _FakeRedis:
    """In-memory Redis covering set(ex=)/delete/exists/mget/aclose."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value if isinstance(value, str) else value.decode()
        if ex is not None:
            self.ttls[key] = ex

    async def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)
        return 1

    async def exists(self, key):
        return int(key in self.store)

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    async def aclose(self):
        pass


class _FailingRedis:
    """Redis that raises on every call — simulates a Redis outage."""

    async def mget(self, keys):
        raise ConnectionError("redis down")

    async def set(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def delete(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def exists(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def aclose(self):
        pass


def _key(uid) -> str:
    return f"llm_gateway:upstream:unhealthy:{uid}"


# --- mark_unhealthy ---------------------------------------------------------


async def test_mark_unhealthy_sets_key_with_ttl_and_payload():
    redis = _FakeRedis()
    uid = uuid4()

    await upstream_health.mark_unhealthy(
        redis, uid, reason="connect_timeout", status_code=None, ttl_seconds=30
    )

    key = _key(uid)
    assert key in redis.store
    assert redis.ttls[key] == 30
    payload = json.loads(redis.store[key])
    assert payload["reason"] == "connect_timeout"
    assert payload["status_code"] is None
    assert "since" in payload  # ISO timestamp


async def test_mark_unhealthy_refreshes_ttl_on_repeat():
    """Idempotent: a repeated failed probe refreshes the TTL, doesn't stack."""
    redis = _FakeRedis()
    uid = uuid4()

    await upstream_health.mark_unhealthy(
        redis, uid, reason="http_5xx", status_code=500, ttl_seconds=30
    )
    await upstream_health.mark_unhealthy(
        redis, uid, reason="http_5xx", status_code=500, ttl_seconds=30
    )

    assert len([k for k in redis.store if _key(uid) in k]) == 1
    assert redis.ttls[_key(uid)] == 30


# --- clear_unhealthy --------------------------------------------------------


async def test_clear_unhealthy_deletes_marker():
    redis = _FakeRedis()
    uid = uuid4()
    redis.store[_key(uid)] = '{"reason":"http_5xx"}'

    await upstream_health.clear_unhealthy(redis, uid)

    assert _key(uid) not in redis.store


async def test_clear_unhealthy_on_healthy_upstream_is_noop():
    """Deleting a non-existent marker is a no-op (already healthy)."""
    redis = _FakeRedis()
    uid = uuid4()

    await upstream_health.clear_unhealthy(redis, uid)

    assert _key(uid) not in redis.store


# --- is_unhealthy -----------------------------------------------------------


async def test_is_unhealthy_true_when_marker_present():
    redis = _FakeRedis()
    uid = uuid4()
    redis.store[_key(uid)] = '{"reason":"http_5xx"}'

    assert await upstream_health.is_unhealthy(redis, uid) is True


async def test_is_unhealthy_false_when_marker_absent():
    redis = _FakeRedis()
    uid = uuid4()

    assert await upstream_health.is_unhealthy(redis, uid) is False


# --- filter_unhealthy -------------------------------------------------------


async def test_filter_unhealthy_returns_only_unhealthy_ids():
    redis = _FakeRedis()
    healthy_uid = uuid4()
    unhealthy_uid = uuid4()
    redis.store[_key(unhealthy_uid)] = '{"reason":"connect_timeout"}'

    result = await upstream_health.filter_unhealthy(
        redis, [healthy_uid, unhealthy_uid]
    )

    assert result == {str(unhealthy_uid)}
    assert str(healthy_uid) not in result


async def test_filter_unhealthy_empty_input_returns_empty_set():
    redis = _FakeRedis()
    assert await upstream_health.filter_unhealthy(redis, []) == set()


async def test_filter_unhealthy_redis_none_degrades_open():
    """redis=None → empty set (trust PG config alone, don't fail closed).

    A Redis outage must not take down the data plane. The routing path falls
    back to PG configuration state, accepting that a stale-unhealthy upstream
    may receive traffic until Redis recovers.
    """
    result = await upstream_health.filter_unhealthy(None, [uuid4(), uuid4()])
    assert result == set()


async def test_filter_unhealthy_redis_error_degrades_open():
    """A Redis exception → empty set + logged, not raised.

    Same rationale as redis=None: the routing path must survive a Redis blip
    without rejecting requests.
    """
    redis = _FailingRedis()
    result = await upstream_health.filter_unhealthy(redis, [uuid4(), uuid4()])
    assert result == set()


async def test_filter_unhealthy_accepts_string_ids():
    """UpstreamTarget.id is a UUID, but filter must also accept str (MGET keys)."""
    redis = _FakeRedis()
    uid_str = str(uuid4())
    redis.store[_key(uid_str)] = '{"reason":"http_5xx"}'

    result = await upstream_health.filter_unhealthy(redis, [uid_str])

    assert result == {uid_str}


# --- parse_payload ----------------------------------------------------------


def test_parse_payload_decodes_json():
    raw = json.dumps({"reason": "connect_timeout", "status_code": None})
    parsed = upstream_health.parse_payload(raw)
    assert parsed["reason"] == "connect_timeout"


def test_parse_payload_none_returns_none():
    assert upstream_health.parse_payload(None) is None


def test_parse_payload_bytes_decodes():
    raw = json.dumps({"reason": "http_5xx"}).encode("utf-8")
    parsed = upstream_health.parse_payload(raw)
    assert parsed["reason"] == "http_5xx"


def test_parse_payload_corrupt_returns_raw():
    parsed = upstream_health.parse_payload("not json{")
    assert parsed == {"raw": "not json{"}


def test_parse_payload_non_dict_json_returns_raw():
    """A JSON array/scalar is not a valid marker payload; wrap it as raw."""
    parsed = upstream_health.parse_payload("[1, 2, 3]")
    assert parsed == {"raw": "[1, 2, 3]"}
