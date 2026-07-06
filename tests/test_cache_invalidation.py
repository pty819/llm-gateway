"""Regression: policy_cache invalidation after RatePolicy changes.

Covers the gap where admin rate-policy edits took up to 30s to take
effect because policy_cache had no invalidation path. Two layers:

* Layer 1 — the cache key is versioned by ``subject.updated_at`` so any
  subject modification rotates the key naturally.
* Layer 2 — the RatePolicy create/update admin handlers explicitly call
  ``policy_cache.invalidate("rate:")`` because policy edits do not touch
  Subject and therefore do not rotate the versioned key.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def _clear_policy_cache():
    """Each test starts with an empty policy_cache."""
    from llm_gateway.services.cache import policy_cache

    policy_cache._store.clear()
    yield
    policy_cache._store.clear()


async def test_rate_policy_create_invalidates_cache(gateway_fixture, client):
    """Layer 2: explicit invalidate on RatePolicy write.

    Goes through the real admin HTTP endpoint so the invalidate call in
    ``create_rate_policy`` actually fires.
    """
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.rate_limit import resolve_effective_rate_policy

    settings = get_settings()

    # Prime the cache: resolve once with no policy -> returns defaults.
    async with AsyncSessionLocal() as session:
        effective = await resolve_effective_rate_policy(
            session,
            key_id=gateway_fixture.key_id,
            subject_id=gateway_fixture.subject_id,
            project_id=gateway_fixture.project_id,
            defaults=settings,
        )
    default_rpm = effective.requests_per_minute
    # Cache now holds the default.
    from llm_gateway.services.cache import policy_cache

    assert policy_cache._store, "cache should have been primed"

    # Create a restrictive RatePolicy at key scope through the admin endpoint.
    admin_headers = {"x-admin-token": settings.admin_token}
    response = await client.post(
        "/admin/rate-policies",
        json={
            "scope": "key",
            "scope_id": str(gateway_fixture.key_id),
            "requests_per_minute": 1,  # very restrictive
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    # Re-resolve: must reflect the new restrictive limit immediately,
    # NOT the cached default. If invalidate worked, the old cache entry
    # is gone and we recompute.
    async with AsyncSessionLocal() as session:
        effective = await resolve_effective_rate_policy(
            session,
            key_id=gateway_fixture.key_id,
            subject_id=gateway_fixture.subject_id,
            project_id=gateway_fixture.project_id,
            defaults=settings,
        )
    assert effective.requests_per_minute == 1, (
        f"RatePolicy change did not take effect; got {effective.requests_per_minute}, "
        f"expected 1 (default was {default_rpm}). policy_cache invalidate may be missing."
    )


async def test_subject_updated_at_rotates_cache_key(gateway_fixture):
    """Layer 1: subject.updated_at versioning rotates the cache key.

    When a subject's updated_at changes, the policy_cache key changes, so
    the previously cached EffectiveRatePolicy is not served.
    """
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.models import RatePolicy, ResourceState, Subject, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.cache import policy_cache
    from llm_gateway.services.rate_limit import resolve_effective_rate_policy

    settings = get_settings()

    # Create a policy at subject scope so resolution is non-default.
    async with AsyncSessionLocal() as session:
        policy = RatePolicy(
            scope="subject",
            scope_id=gateway_fixture.subject_id,
            requests_per_minute=5,
            state=ResourceState.ACTIVE,
        )
        session.add(policy)
        await session.commit()

    # Prime cache.
    async with AsyncSessionLocal() as session:
        await resolve_effective_rate_policy(
            session,
            key_id=gateway_fixture.key_id,
            subject_id=gateway_fixture.subject_id,
            project_id=gateway_fixture.project_id,
            defaults=settings,
        )
    cached_keys_before = set(policy_cache._store.keys())

    # Bump the subject's updated_at (same pattern as api/admin/identity.py).
    # The epoch is whole-second resolution, so nudge forward past the current
    # second to guarantee a distinct epoch even under sub-second test timing.
    async with AsyncSessionLocal() as session:
        subject = await session.get(Subject, gateway_fixture.subject_id)
        new_updated_at = utcnow().replace(microsecond=0) + timedelta(seconds=90)
        subject.updated_at = new_updated_at
        await session.commit()

    # Re-resolve — should use a DIFFERENT cache key (different epoch).
    async with AsyncSessionLocal() as session:
        await resolve_effective_rate_policy(
            session,
            key_id=gateway_fixture.key_id,
            subject_id=gateway_fixture.subject_id,
            project_id=gateway_fixture.project_id,
            defaults=settings,
        )
    cached_keys_after = set(policy_cache._store.keys())

    new_keys = cached_keys_after - cached_keys_before
    assert new_keys, (
        "No new cache key created after subject updated_at bump — "
        "subject-epoch versioning may not be applied to the cache key."
    )


async def test_policy_cache_key_format_includes_epoch_unit():
    """Belt-and-suspenders unit check: a cached key has a trailing epoch.

    This catches a regression where someone reverts the versioning but the
    DB-driven integration test happens to see no new keys for some other
    reason (e.g. timing/floor to same second). Independent of DB state.
    """
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from llm_gateway.db.models import Subject
    from llm_gateway.services.cache import policy_cache
    from llm_gateway.services.rate_limit import resolve_effective_rate_policy

    settings = AsyncMock()
    settings.default_request_limit_per_minute = 100
    settings.default_concurrency_limit = 10

    subject_id = uuid4()
    # A subject created at a known epoch.
    fixed_subject = Subject(id=subject_id, name="u", type="user")
    fixed_subject.updated_at = datetime(2025, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    expected_epoch = int(fixed_subject.updated_at.timestamp())

    # session.get returns our fixed subject; session.execute returns no policies.
    session = AsyncMock()
    session.get = AsyncMock(return_value=fixed_subject)

    async def fake_execute(_stmt):
        class _Result:
            def scalars(self):
                class _Scalars:
                    def all(self):
                        return []

                return _Scalars()

        return _Result()

    session.execute = fake_execute

    await resolve_effective_rate_policy(
        session,
        key_id=uuid4(),
        subject_id=subject_id,
        project_id=uuid4(),
        defaults=settings,
    )

    cached_keys = list(policy_cache._store.keys())
    assert cached_keys, "nothing was cached"
    key = cached_keys[0]
    # Key must end with the subject epoch as a colon-separated component.
    assert key.endswith(f":{expected_epoch}"), (
        f"cache key {key!r} is not versioned by subject epoch; expected to end "
        f"with ':{expected_epoch}'"
    )
