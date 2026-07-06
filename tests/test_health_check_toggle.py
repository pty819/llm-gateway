"""Tests for the admin health-check runtime toggle.

The toggle uses a Redis override key: SET "0" to force-disable, DEL to withdraw
the override and fall back to the env-var default. The sidecar re-reads this
every cycle in _main_loop, so a toggle takes effect within one interval.

These tests cover:
- effective_enabled resolves Redis override > env default
- set_enabled_override writes/deletes the Redis key
- GET /admin/health-check returns the effective state + source
- PATCH /admin/health-check toggles the override + writes audit
- _main_loop skips _run_once when disabled, resumes when re-enabled
"""

from __future__ import annotations

import asyncio

import pytest


class _FakeRedis:
    """In-memory Redis covering get/set/delete/exists/aclose."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value if isinstance(value, str) else value.decode()

    async def delete(self, key):
        return self.store.pop(key, None) is not None

    async def exists(self, key):
        return int(key in self.store)

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    async def aclose(self):
        pass


_OVERRIDE_KEY = "llm_gateway:health_check:enabled"


# --- effective_enabled + override helpers (unit) ----------------------------


async def test_effective_enabled_no_override_falls_back_to_env(monkeypatch):
    """No Redis override → use the env-var default."""
    from llm_gateway.services import health_checker

    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: True)
    redis = _FakeRedis()

    enabled, source = await health_checker.effective_enabled(redis)

    assert enabled is True
    assert source == "env_default"


async def test_effective_enabled_override_disables(monkeypatch):
    """Redis override "0" → disabled regardless of env default."""
    from llm_gateway.services import health_checker

    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: True)
    redis = _FakeRedis()
    redis.store[_OVERRIDE_KEY] = "0"

    enabled, source = await health_checker.effective_enabled(redis)

    assert enabled is False
    assert source == "redis_override"


async def test_set_enabled_override_false_writes_sentinel(monkeypatch):
    """set_enabled_override(False) writes the "0" sentinel."""
    from llm_gateway.services import health_checker

    redis = _FakeRedis()
    await health_checker.set_enabled_override(redis, enabled=False)

    assert redis.store[_OVERRIDE_KEY] == "0"


async def test_set_enabled_override_true_deletes_key(monkeypatch):
    """set_enabled_override(True) withdraws the override (DEL key)."""
    from llm_gateway.services import health_checker

    redis = _FakeRedis()
    redis.store[_OVERRIDE_KEY] = "0"

    await health_checker.set_enabled_override(redis, enabled=True)

    assert _OVERRIDE_KEY not in redis.store


async def test_clear_enabled_override_removes_key():
    from llm_gateway.services import health_checker

    redis = _FakeRedis()
    redis.store[_OVERRIDE_KEY] = "0"

    await health_checker.clear_enabled_override(redis)

    assert _OVERRIDE_KEY not in redis.store


# --- _main_loop respects runtime toggle ------------------------------------


async def test_main_loop_skips_run_once_when_disabled(monkeypatch):
    """When effective_enabled=False, _main_loop must NOT call _run_once.

    The loop still runs (so it can resume when re-enabled) — it just skips the
    probe cycle. This is the core of the runtime toggle: disable takes effect
    next cycle without restarting the sidecar.
    """
    from llm_gateway.services import health_checker

    redis = _FakeRedis()
    redis.store[_OVERRIDE_KEY] = "0"  # force disabled

    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: True)
    monkeypatch.setattr(health_checker, "_settings_interval", lambda: 0.01)
    monkeypatch.setattr(health_checker, "_settings_timeout", lambda: 3.0)
    monkeypatch.setattr(health_checker, "_settings_unhealthy_ttl", lambda: 30)
    monkeypatch.setattr(health_checker, "_settings_quorum_min", lambda: 2)
    monkeypatch.setattr(health_checker, "_build_redis", lambda: redis)

    run_once_calls = []

    async def _fake_run_once(*, redis, timeout_seconds, unhealthy_ttl_seconds, quorum_min):
        run_once_calls.append(1)

    monkeypatch.setattr(health_checker, "_run_once", _fake_run_once)

    # Run the loop briefly
    task = asyncio.create_task(health_checker._main_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert run_once_calls == []  # disabled → no probes


async def test_main_loop_runs_when_enabled(monkeypatch):
    """When effective_enabled=True, _main_loop calls _run_once each cycle."""
    from llm_gateway.services import health_checker

    redis = _FakeRedis()  # no override → env default

    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: True)
    monkeypatch.setattr(health_checker, "_settings_interval", lambda: 0.01)
    monkeypatch.setattr(health_checker, "_settings_timeout", lambda: 3.0)
    monkeypatch.setattr(health_checker, "_settings_unhealthy_ttl", lambda: 30)
    monkeypatch.setattr(health_checker, "_settings_quorum_min", lambda: 2)
    monkeypatch.setattr(health_checker, "_build_redis", lambda: redis)

    run_once_calls = []

    async def _fake_run_once(*, redis, timeout_seconds, unhealthy_ttl_seconds, quorum_min):
        run_once_calls.append(1)

    monkeypatch.setattr(health_checker, "_run_once", _fake_run_once)

    task = asyncio.create_task(health_checker._main_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(run_once_calls) >= 1  # enabled → probes ran


async def test_main_loop_resumes_after_reenable(monkeypatch):
    """Toggle from disabled→enabled mid-loop: probes resume next cycle.

    This is the key UX requirement: admin clicks "开启巡检" and within one
    interval the sidecar resumes probing, no restart needed.
    """
    from llm_gateway.services import health_checker

    redis = _FakeRedis()
    redis.store[_OVERRIDE_KEY] = "0"  # start disabled

    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: True)
    monkeypatch.setattr(health_checker, "_settings_interval", lambda: 0.02)
    monkeypatch.setattr(health_checker, "_settings_timeout", lambda: 3.0)
    monkeypatch.setattr(health_checker, "_settings_unhealthy_ttl", lambda: 30)
    monkeypatch.setattr(health_checker, "_settings_quorum_min", lambda: 2)
    monkeypatch.setattr(health_checker, "_build_redis", lambda: redis)

    run_once_calls = []

    async def _fake_run_once(*, redis, timeout_seconds, unhealthy_ttl_seconds, quorum_min):
        run_once_calls.append(1)

    monkeypatch.setattr(health_checker, "_run_once", _fake_run_once)

    task = asyncio.create_task(health_checker._main_loop())
    # Let it run disabled for a bit
    await asyncio.sleep(0.05)
    assert run_once_calls == []  # disabled, no probes

    # Admin re-enables
    await health_checker.set_enabled_override(redis, enabled=True)
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(run_once_calls) >= 1  # resumed after re-enable


# --- Admin API endpoints (integration via TestClient) ----------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_get_health_check_config_returns_env_default(client, monkeypatch):
    """GET /admin/health-check with no override → env default."""
    from llm_gateway.services.rate_limit import redis_client

    # Ensure no override
    await redis_client.delete(_OVERRIDE_KEY)

    response = await client.get(
        "/admin/health-check",
        headers={"x-admin-token": "dev-admin-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "env_default"
    assert body["enabled"] is True  # default is True


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_health_check_disable_writes_override(client):
    """PATCH enabled=false → Redis override set, source becomes redis_override."""
    from llm_gateway.services.rate_limit import redis_client

    await redis_client.delete(_OVERRIDE_KEY)  # clean slate

    response = await client.patch(
        "/admin/health-check",
        headers={"x-admin-token": "dev-admin-token"},
        json={"enabled": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["source"] == "redis_override"

    # Override actually in Redis
    assert await redis_client.get(_OVERRIDE_KEY) == "0"

    # Cleanup
    await redis_client.delete(_OVERRIDE_KEY)


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_health_check_enable_removes_override(client):
    """PATCH enabled=true → override deleted, falls back to env default."""
    from llm_gateway.services.rate_limit import redis_client

    # Start with override active (disabled)
    await redis_client.set(_OVERRIDE_KEY, "0")

    response = await client.patch(
        "/admin/health-check",
        headers={"x-admin-token": "dev-admin-token"},
        json={"enabled": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["source"] == "env_default"

    # Override gone
    assert await redis_client.get(_OVERRIDE_KEY) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_health_check_writes_audit(client):
    """PATCH toggles must record an audit event for traceability."""
    from sqlalchemy import select

    from llm_gateway.db.models import AuditEvent
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.rate_limit import redis_client

    await redis_client.delete(_OVERRIDE_KEY)

    await client.patch(
        "/admin/health-check",
        headers={"x-admin-token": "dev-admin-token"},
        json={"enabled": False},
    )

    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AuditEvent)
                    .where(AuditEvent.action == "health_check.toggle")
                    .order_by(AuditEvent.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        assert rows, "expected a health_check.toggle audit row"
        latest = rows[0]
        assert latest.outcome == "disabled"
        assert latest.detail.get("enabled") is False

    # Cleanup
    await redis_client.delete(_OVERRIDE_KEY)
