from uuid import uuid4

import pytest
from redis.exceptions import RedisError

import llm_gateway.services.rate_limit as rate_limit
from llm_gateway.services.rate_limit import (
    RateLimitExceeded,
    acquire_concurrency_slot,
    check_request_rate,
)


class _BrokenRedis:
    """Simulates a Redis that is unavailable for every rate-limit operation."""

    async def incr(self, *args, **kwargs):
        raise RedisError("broken")

    async def expire(self, *args, **kwargs):
        raise RedisError("broken")

    def eval(self, *args, **kwargs):
        async def _raise():
            raise RedisError("broken")

        return _raise()


class _Settings:
    def __init__(self, fail_closed: bool):
        self.rate_limit_fail_closed = fail_closed


async def test_check_rate_fails_closed(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_settings", lambda: _Settings(True))
    with pytest.raises(RateLimitExceeded):
        await check_request_rate(_BrokenRedis(), key_id=uuid4(), limit=10)


async def test_check_rate_fails_open_when_configured(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_settings", lambda: _Settings(False))
    # Must not raise — request is allowed through.
    await check_request_rate(_BrokenRedis(), key_id=uuid4(), limit=10)


async def test_acquire_slot_fails_closed(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_settings", lambda: _Settings(True))
    with pytest.raises(RateLimitExceeded):
        await acquire_concurrency_slot(_BrokenRedis(), key_id=uuid4(), limit=1)


async def test_acquire_slot_fails_open_returns_empty_token(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_settings", lambda: _Settings(False))
    token = await acquire_concurrency_slot(_BrokenRedis(), key_id=uuid4(), limit=1)
    assert token == ""

