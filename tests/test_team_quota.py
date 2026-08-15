"""Team token quota: window math, per-member budgets, check-any/charge-all,
hot-path integration."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from llm_gateway.db.models import (
    EndpointFamily,
    RequestOutcome,
    ResourceState,
    Team,
    TeamMembership,
    TeamTokenQuota,
)
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services import team_quota
from llm_gateway.services.litellm_client import LiteLLMCallResult
from llm_gateway.services.rate_limit import RateLimitExceeded
from llm_gateway.services.team_quota import (
    TeamQuotaContext,
    charge_team_quota,
    check_team_quota,
    counter_key,
    current_window,
    resolve_team_quota,
    token_total_from_usage,
)

from conftest import fetch_request_fact

pytestmark = pytest.mark.asyncio(loop_scope="session")

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _at(hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 8, 15, hh, mm, tzinfo=SHANGHAI)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [str(self.values.get(k)) if k in self.values else None for k in keys]

    async def get(self, key: str) -> str | None:
        return str(self.values[key]) if key in self.values else None

    async def incrby(self, key: str, amount: int) -> int:
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.ttls[key] = ttl


class BrokenRedis:
    async def mget(self, keys: list[str]) -> list[str | None]:
        from redis.exceptions import RedisError

        raise RedisError("down")

    async def incrby(self, key: str, amount: int) -> int:
        from redis.exceptions import RedisError

        raise RedisError("down")

    async def expire(self, key: str, ttl: int) -> None:
        return None


def _ctx(
    pools: list[tuple[UUID, int]],
    window: str = "morning",
    subject_id: UUID | None = None,
) -> TeamQuotaContext:
    return TeamQuotaContext(
        subject_id=subject_id or UUID(int=99),
        window=window,
        window_date=date(2026, 8, 15),
        window_end=datetime(2026, 8, 15, 13, 0, tzinfo=SHANGHAI),
        pools=pools,
    )


# ---------------------------------------------------------------------------
# Window math
# ---------------------------------------------------------------------------


def test_window_boundaries():
    tz = SHANGHAI
    assert current_window(_at(7, 59), tz=tz) == ("evening", datetime(2026, 8, 14, tzinfo=tz).date(), _at(8, 0))
    assert current_window(_at(8, 0), tz=tz)[0] == "morning"
    assert current_window(_at(12, 59), tz=tz)[0] == "morning"
    assert current_window(_at(13, 0), tz=tz)[0] == "afternoon"
    assert current_window(_at(17, 59), tz=tz)[0] == "afternoon"
    assert current_window(_at(18, 0), tz=tz)[0] == "evening"
    assert current_window(_at(23, 30), tz=tz)[0] == "evening"
    # After midnight belongs to the previous day's evening window.
    window, window_date, end = current_window(datetime(2026, 8, 16, 0, 5, tzinfo=tz), tz=tz)
    assert window == "evening"
    assert window_date == datetime(2026, 8, 15, tzinfo=tz).date()
    assert end == datetime(2026, 8, 16, 8, 0, tzinfo=tz)


def test_window_converts_foreign_timezone():
    # 09:00 UTC == 17:00 Shanghai -> afternoon, not evening.
    moment = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
    assert current_window(moment, tz=SHANGHAI)[0] == "afternoon"


# ---------------------------------------------------------------------------
# check-any / charge-all
# ---------------------------------------------------------------------------


async def test_check_passes_when_any_pool_has_remaining():
    redis = FakeRedis()
    quota = _ctx([(UUID(int=1), 100), (UUID(int=2), 50)])
    redis.values[team_quota._counter_key(quota, UUID(int=1))] = 100  # exhausted
    redis.values[team_quota._counter_key(quota, UUID(int=2))] = 10  # remaining
    await check_team_quota(redis, quota)  # no raise


async def test_check_raises_when_all_pools_exhausted():
    redis = FakeRedis()
    quota = _ctx([(UUID(int=1), 100), (UUID(int=2), 50)])
    redis.values[team_quota._counter_key(quota, UUID(int=1))] = 150
    redis.values[team_quota._counter_key(quota, UUID(int=2))] = 50
    with pytest.raises(RateLimitExceeded, match="team_token_quota_exceeded"):
        await check_team_quota(redis, quota)


async def test_check_passes_when_no_counters_yet():
    await check_team_quota(FakeRedis(), _ctx([(UUID(int=1), 100)]))


async def test_check_empty_context_passes():
    await check_team_quota(FakeRedis(), _ctx([]))


async def test_charge_all_pools_with_ttl():
    redis = FakeRedis()
    quota = _ctx([(UUID(int=1), 100), (UUID(int=2), 50)])
    await charge_team_quota(redis, quota, 60)
    assert redis.values[team_quota._counter_key(quota, UUID(int=1))] == 60
    assert redis.values[team_quota._counter_key(quota, UUID(int=2))] == 60
    assert set(redis.ttls) == {
        team_quota._counter_key(quota, UUID(int=1)),
        team_quota._counter_key(quota, UUID(int=2)),
    }
    # Second charge does not overwrite TTLs (expire only fires on key creation).
    await charge_team_quota(redis, quota, 10)
    assert redis.values[team_quota._counter_key(quota, UUID(int=1))] == 70


async def test_charge_noop_for_zero_tokens():
    redis = FakeRedis()
    await charge_team_quota(redis, _ctx([(UUID(int=1), 100)]), 0)
    assert redis.values == {}


async def test_same_team_members_have_independent_budgets():
    """组上限对每个成员分别生效:同一组的两个人各自有自己的池。"""
    team = UUID(int=1)
    alice = UUID(int=11)
    bob = UUID(int=12)
    redis = FakeRedis()
    alice_ctx = _ctx([(team, 100)], subject_id=alice)
    bob_ctx = _ctx([(team, 100)], subject_id=bob)

    # Alice 用满 100,还能超出一点(check-before-charge)。
    await charge_team_quota(redis, alice_ctx, 100)
    with pytest.raises(RateLimitExceeded, match="team_token_quota_exceeded"):
        await check_team_quota(redis, alice_ctx)

    # Bob 的预算完全不受 Alice 影响。
    await check_team_quota(redis, bob_ctx)  # no raise
    await charge_team_quota(redis, bob_ctx, 60)
    await check_team_quota(redis, bob_ctx)  # still has 40

    assert redis.values[counter_key(team, alice, "morning", date(2026, 8, 15))] == 100
    assert redis.values[counter_key(team, bob, "morning", date(2026, 8, 15))] == 60


async def test_multi_team_member_enjoys_largest_budget():
    """A=400、B=500:同属两组的人合计可用到 500(取更大池,charge-all)。"""
    team_a, team_b = UUID(int=1), UUID(int=2)
    member = UUID(int=99)
    redis = FakeRedis()
    ctx = _ctx([(team_a, 400), (team_b, 500)], subject_id=member)

    await charge_team_quota(redis, ctx, 420)  # A 已耗尽,B 还剩 80
    await check_team_quota(redis, ctx)  # no raise: B 仍有余量

    await charge_team_quota(redis, ctx, 80)  # 合计 500,两池全耗尽
    with pytest.raises(RateLimitExceeded, match="team_token_quota_exceeded"):
        await check_team_quota(redis, ctx)


async def test_charge_swallows_redis_errors():
    await charge_team_quota(BrokenRedis(), _ctx([(UUID(int=1), 100)]), 50)  # no raise


async def test_check_redis_failure_follows_fail_closed(monkeypatch):
    monkeypatch.setattr(
        team_quota, "get_settings", lambda: type("S", (), {"rate_limit_fail_closed": True})()
    )
    with pytest.raises(RateLimitExceeded, match="team_token_quota_unavailable"):
        await check_team_quota(BrokenRedis(), _ctx([(UUID(int=1), 100)]))

    monkeypatch.setattr(
        team_quota, "get_settings", lambda: type("S", (), {"rate_limit_fail_closed": False})()
    )
    await check_team_quota(BrokenRedis(), _ctx([(UUID(int=1), 100)]))  # fail-open


# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------


def test_token_total_prefers_total_tokens():
    assert token_total_from_usage({"total_tokens": 91, "prompt_tokens": 80, "output_tokens": 11}) == 91


def test_token_total_falls_back_to_prompt_plus_completion():
    assert token_total_from_usage({"prompt_tokens": 80, "completion_tokens": 11}) == 91
    assert token_total_from_usage({"input_tokens": 7, "output_tokens": 3}) == 10


def test_token_total_unknown_is_zero():
    assert token_total_from_usage(None) == 0
    assert token_total_from_usage({}) == 0


# ---------------------------------------------------------------------------
# Resolution (real test DB)
# ---------------------------------------------------------------------------


async def _seed_team_with_quota(
    *,
    subject_id: UUID,
    model_alias_id: UUID,
    limits: tuple[int | None, int | None, int | None],
    state: ResourceState = ResourceState.ACTIVE,
    membership_state: ResourceState = ResourceState.ACTIVE,
    grant: bool = True,
) -> UUID:
    async with AsyncSessionLocal() as session:
        team = Team(name=f"quota-team-{uuid4().hex[:12]}")
        session.add(team)
        await session.flush()
        session.add(
            TeamMembership(
                team_id=team.id, subject_id=subject_id, state=membership_state
            )
        )
        if grant:
            from llm_gateway.db.models import ModelTeamGrant

            session.add(ModelTeamGrant(team_id=team.id, model_alias_id=model_alias_id))
        session.add(
            TeamTokenQuota(
                team_id=team.id,
                morning_tokens=limits[0],
                afternoon_tokens=limits[1],
                evening_tokens=limits[2],
                state=state,
            )
        )
        await session.commit()
        return team.id


async def test_resolve_collects_only_fully_qualifying_teams(gateway_fixture):
    subject_id = gateway_fixture.subject_id
    model_id = gateway_fixture.model_alias_id
    await _seed_team_with_quota(
        subject_id=subject_id, model_alias_id=model_id, limits=(100, 100, 100)
    )
    # NULL limit for every window -> not a candidate.
    await _seed_team_with_quota(
        subject_id=subject_id, model_alias_id=model_id, limits=(None, None, None)
    )
    # DISABLED quota row -> not a candidate.
    await _seed_team_with_quota(
        subject_id=subject_id,
        model_alias_id=model_id,
        limits=(100, 100, 100),
        state=ResourceState.DISABLED,
    )
    # No model grant -> not a candidate.
    await _seed_team_with_quota(
        subject_id=subject_id, model_alias_id=model_id, limits=(100, 100, 100), grant=False
    )

    async with AsyncSessionLocal() as session:
        quota = await resolve_team_quota(
            session, subject_id=subject_id, model_alias_id=model_id
        )
    assert quota is not None
    assert len(quota.pools) == 1
    assert quota.pools[0][1] == 100


async def test_resolve_returns_none_without_quota(gateway_fixture):
    async with AsyncSessionLocal() as session:
        quota = await resolve_team_quota(
            session,
            subject_id=uuid4(),  # belongs to nothing
            model_alias_id=gateway_fixture.model_alias_id,
        )
    assert quota is None


# ---------------------------------------------------------------------------
# Hot-path integration (real app, fake upstream, real Redis)
# ---------------------------------------------------------------------------


async def _drain_quota_counters(*team_ids: UUID) -> None:
    from llm_gateway.services.rate_limit import redis_client

    for team_id in team_ids:
        keys = []
        async for key in redis_client.scan_iter(
            match=f"llm_gateway:quota:tokens:{team_id}:*"
        ):
            keys.append(key)
        if keys:
            await redis_client.delete(*keys)


async def test_quota_blocks_requests_when_all_pools_exhausted(
    client, gateway_fixture, monkeypatch
):
    usage = {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}

    async def fake_upstream_request_once(*, endpoint_family, model_alias, upstream, body):
        return LiteLLMCallResult(
            response={"id": "resp_q", "status": "completed", "output": []},
            usage=dict(usage),
        )

    monkeypatch.setattr(
        "llm_gateway.api.proxy.upstream_request_once", fake_upstream_request_once
    )

    team_id = await _seed_team_with_quota(
        subject_id=gateway_fixture.subject_id,
        model_alias_id=gateway_fixture.model_alias_id,
        limits=(100, 100, 100),  # same limit in every window: test is time-agnostic
    )
    try:
        headers = {
            "Authorization": f"Bearer {gateway_fixture.raw_key}",
        }

        async def post(request_id: str):
            return await client.post(
                "/v1/chat/completions",
                headers={**headers, "x-request-id": request_id},
                json={
                    "model": gateway_fixture.model_alias,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )

        r1 = await post(f"pytest-quota-{uuid4()}")
        assert r1.status_code == 200, r1.text
        r2 = await post(f"pytest-quota-{uuid4()}")
        assert r2.status_code == 200, r2.text  # admission saw 60 < 100
        r3 = await post(f"pytest-quota-{uuid4()}")
        assert r3.status_code == 429
        assert r3.json()["detail"] == "team_token_quota_exceeded"
    finally:
        await _drain_quota_counters(team_id)


async def test_quota_denial_records_rate_limited_fact(
    client, gateway_fixture, monkeypatch
):
    usage = {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}

    async def fake_upstream_request_once(*, endpoint_family, model_alias, upstream, body):
        return LiteLLMCallResult(
            response={"id": "resp_q2", "status": "completed", "output": []},
            usage=dict(usage),
        )

    monkeypatch.setattr(
        "llm_gateway.api.proxy.upstream_request_once", fake_upstream_request_once
    )

    team_id = await _seed_team_with_quota(
        subject_id=gateway_fixture.subject_id,
        model_alias_id=gateway_fixture.model_alias_id,
        limits=(10, 10, 10),  # first request charges past it; second is denied
    )
    try:
        base_headers = {
            "Authorization": f"Bearer {gateway_fixture.raw_key}",
        }

        first = await client.post(
            "/v1/chat/completions",
            headers={**base_headers, "x-request-id": f"pytest-quota-fact1-{uuid4()}"},
            json={
                "model": gateway_fixture.model_alias,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert first.status_code == 200, first.text  # admission is pre-charge

        request_id = f"pytest-quota-fact-{uuid4()}"
        response = await client.post(
            "/v1/chat/completions",
            headers={**base_headers, "x-request-id": request_id},
            json={
                "model": gateway_fixture.model_alias,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status_code == 429

        fact = await fetch_request_fact(request_id)
        assert fact.outcome == RequestOutcome.RATE_LIMITED
        assert fact.error_detail == "team_token_quota_exceeded"
    finally:
        await _drain_quota_counters(team_id)


async def test_multi_team_charges_every_pool(
    client, gateway_fixture, monkeypatch
):
    usage = {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}

    async def fake_upstream_request_once(*, endpoint_family, model_alias, upstream, body):
        return LiteLLMCallResult(
            response={"id": "resp_q3", "status": "completed", "output": []},
            usage=dict(usage),
        )

    monkeypatch.setattr(
        "llm_gateway.api.proxy.upstream_request_once", fake_upstream_request_once
    )

    team_a = await _seed_team_with_quota(
        subject_id=gateway_fixture.subject_id,
        model_alias_id=gateway_fixture.model_alias_id,
        limits=(1000, 1000, 1000),
    )
    team_b = await _seed_team_with_quota(
        subject_id=gateway_fixture.subject_id,
        model_alias_id=gateway_fixture.model_alias_id,
        limits=(1000, 1000, 1000),
    )
    try:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {gateway_fixture.raw_key}",
                "x-request-id": f"pytest-quota-multi-{uuid4()}",
            },
            json={
                "model": gateway_fixture.model_alias,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status_code == 200, response.text

        from llm_gateway.services.rate_limit import redis_client

        window, window_date, _end = current_window()
        for team_id in (team_a, team_b):
            key = counter_key(team_id, gateway_fixture.subject_id, window, window_date)
            used = await redis_client.get(key)
            assert used is not None and int(used) == 60, (team_id, used)
    finally:
        await _drain_quota_counters(team_a, team_b)


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


async def test_admin_token_quota_roundtrip(client):
    async with AsyncSessionLocal() as session:
        team = Team(name=f"quota-admin-{uuid4().hex[:12]}")
        session.add(team)
        await session.commit()
        team_id = team.id

    from llm_gateway.api.deps import admin_dep  # noqa: F401  (route exists check)

    headers = {"x-admin-token": "dev-admin-token"}

    put = await client.put(
        f"/admin/teams/{team_id}/token-quota",
        headers=headers,
        json={"morning_tokens": 1000, "afternoon_tokens": 2000, "evening_tokens": 5000},
    )
    assert put.status_code == 200, put.text
    assert put.json()["afternoon_tokens"] == 2000

    single = await client.get(f"/admin/teams/{team_id}/token-quota", headers=headers)
    assert single.status_code == 200
    body = single.json()
    assert body["morning_tokens"] == 1000
    assert body["evening_tokens"] == 5000
    # Every window has a limit, so the current window must report one no
    # matter when the suite runs. Usage is per-member now — there is no
    # team-level "used" number.
    assert body["current_window_limit"] is not None
    assert body["current_window_used"] is None

    listing = await client.get("/admin/team-token-quotas", headers=headers)
    assert listing.status_code == 200
    assert any(row["team_id"] == str(team_id) for row in listing.json()["items"])

    invalid = await client.put(
        f"/admin/teams/{team_id}/token-quota",
        headers=headers,
        json={"morning_tokens": -1},
    )
    assert invalid.status_code == 422
