from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx2 as httpx
import pytest

from llm_gateway.services.health_checker import HealthVerdict, classify_health


@pytest.mark.parametrize(
    ("status_code", "exc", "expected_healthy", "expected_reason"),
    [
        (200, None, True, "ok"),
        (404, None, True, "ok"),
        (500, None, False, "http_5xx"),
        (502, None, False, "http_5xx"),
        (503, None, False, "http_5xx"),
        (401, None, False, "unexpected_status"),
        (403, None, False, "unexpected_status"),
        (400, None, False, "unexpected_status"),
        # Timeout split: connect vs read vs other. connect_timeout is the
        # event-loop-freeze signature (probes never even establish TCP);
        # read_timeout is a genuinely slow upstream.
        (None, httpx.ConnectTimeout("x"), False, "connect_timeout"),
        (None, httpx.ReadTimeout("x"), False, "read_timeout"),
        (None, httpx.PoolTimeout("x"), False, "timeout"),
        (None, httpx.WriteTimeout("x"), False, "timeout"),
        (None, httpx.ConnectError("x"), False, "connection_error"),
        (None, httpx.ReadError("x"), False, "connection_error"),
        (None, RuntimeError("x"), False, "unknown_error"),
    ],
)
def test_classify_health(status_code, exc, expected_healthy, expected_reason):
    verdict = classify_health(status_code, exc=exc)
    assert verdict.healthy is expected_healthy
    assert verdict.reason == expected_reason
    assert verdict.status_code == status_code


class _FakeUpstream:
    """Stand-in for UpstreamTarget with the fields _probe_upstream reads."""

    def __init__(
        self,
        *,
        base_url: str,
        health_path: str = "/models",
        api_key_value: str | None = None,
        api_key_ref: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url
        self.health_path = health_path
        self.api_key_value = api_key_value
        self.api_key_ref = api_key_ref
        self.extra_headers = extra_headers or {}


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _make_fake_client(*, response=None, exc=None):
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *args, **kwargs):
            if exc is not None:
                raise exc
            return response

    return _FakeClient


async def test_probe_upstream_returns_ok_verdict_for_200(monkeypatch):
    from llm_gateway.services import health_checker

    upstream = _FakeUpstream(base_url="http://upstream.local", health_path="/models")
    monkeypatch.setattr(
        health_checker.httpx,
        "AsyncClient",
        _make_fake_client(response=_FakeResponse(200)),
    )

    verdict = await health_checker._probe_upstream(upstream, timeout_seconds=3.0)
    assert verdict == HealthVerdict(healthy=True, status_code=200, reason="ok")


async def test_probe_upstream_returns_http_5xx_verdict(monkeypatch):
    from llm_gateway.services import health_checker

    upstream = _FakeUpstream(base_url="http://upstream.local", health_path="/models")
    monkeypatch.setattr(
        health_checker.httpx,
        "AsyncClient",
        _make_fake_client(response=_FakeResponse(500)),
    )

    verdict = await health_checker._probe_upstream(upstream, timeout_seconds=3.0)
    assert verdict == HealthVerdict(healthy=False, status_code=500, reason="http_5xx")


async def test_probe_upstream_returns_connect_timeout_verdict(monkeypatch):
    from llm_gateway.services import health_checker

    upstream = _FakeUpstream(base_url="http://upstream.local", health_path="/models")
    monkeypatch.setattr(
        health_checker.httpx,
        "AsyncClient",
        _make_fake_client(exc=httpx.ConnectTimeout("timed out")),
    )

    verdict = await health_checker._probe_upstream(upstream, timeout_seconds=3.0)
    assert verdict == HealthVerdict(healthy=False, status_code=None, reason="connect_timeout")


async def test_probe_upstream_injects_authorization_header(monkeypatch):
    """api_key_value/ref 必须以 Bearer 注入，复用 upstream_client._api_key 语义。"""
    from llm_gateway.services import health_checker

    upstream = _FakeUpstream(base_url="http://upstream.local", api_key_value="secret-key")
    captured = {}

    class _InspectClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url, headers=None, **kwargs):
            captured["url"] = url
            captured["headers"] = headers
            return _FakeResponse(200)

    monkeypatch.setattr(health_checker.httpx, "AsyncClient", _InspectClient)

    await health_checker._probe_upstream(upstream, timeout_seconds=3.0)
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert captured["url"] == "http://upstream.local/models"


# --- Redis marker behavior -------------------------------------------------


class _FakeRedis:
    """Minimal in-memory Redis covering set(ex=)/get/delete/exists/mget/aclose."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value if isinstance(value, str) else value.decode()

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        return self.store.pop(key, None) is not None

    async def exists(self, key):
        return int(key in self.store)

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    async def aclose(self):
        pass


class _FakePersistedUpstream:
    """Stand-in for an UpstreamTarget row; carries id/name for the audit detail."""

    def __init__(self):
        self.id = uuid4()
        self.name = "fake-upstream"


def _make_fake_session_local():
    """AsyncSessionLocal fake whose session.commit() is a no-op (audit-only)."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_session_local():
        class _Session:
            async def commit(self_inner):
                return None

        yield _Session()

    return _fake_session_local


def _make_fake_record_audit(recordd):
    async def _fake_record(session, **kwargs):
        recordd.append(kwargs)
        return None

    return _fake_record


async def test_mark_unhealthy_writes_redis_marker_and_audit(monkeypatch):
    """Probe fail → Redis UNHEALTHY marker set + audit row written.

    PG state is NOT touched — runtime liveness lives in Redis now, config state
    in PG. The two never overlap.
    """
    from llm_gateway.services import health_checker

    redis = _FakeRedis()
    upstream = _FakePersistedUpstream()
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(),
    )
    recorded = []
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.record_audit_event",
        _make_fake_record_audit(recorded),
    )

    verdict = HealthVerdict(False, 500, "http_5xx")
    marked = await health_checker._mark_unhealthy(
        redis,
        upstream_id=upstream.id,
        upstream_name=upstream.name,
        verdict=verdict,
        ttl_seconds=30,
    )

    assert marked is True
    # Redis marker present with reason payload
    key = f"llm_gateway:upstream:unhealthy:{upstream.id}"
    assert key in redis.store
    payload = json.loads(redis.store[key])
    assert payload["reason"] == "http_5xx"
    assert payload["status_code"] == 500
    # Audit row written with the new outcome
    assert len(recorded) == 1
    assert recorded[0]["action"] == "upstream.auto_disable"
    assert recorded[0]["resource_id"] == upstream.id
    assert recorded[0]["outcome"] == "unhealthy"
    assert recorded[0]["detail"]["verdict"] == "http_5xx"


async def test_mark_unhealthy_swallows_audit_failure(monkeypatch):
    """A Redis success must survive a transient PG/audit failure — routing keys
    off Redis, so the marker must stand even if the audit row can't be written.
    """
    from contextlib import asynccontextmanager

    from llm_gateway.services import health_checker

    redis = _FakeRedis()
    upstream = _FakePersistedUpstream()

    @asynccontextmanager
    async def _session():
        class _Session:
            async def commit(self):
                raise RuntimeError("pg down")

        yield _Session()

    monkeypatch.setattr("llm_gateway.services.health_checker.AsyncSessionLocal", _session)
    verdict = HealthVerdict(False, 500, "http_5xx")
    # Must not raise
    await health_checker._mark_unhealthy(
        redis,
        upstream_id=upstream.id,
        upstream_name=upstream.name,
        verdict=verdict,
        ttl_seconds=30,
    )
    key = f"llm_gateway:upstream:unhealthy:{upstream.id}"
    assert key in redis.store  # marker set despite audit failure


async def test_clear_healthy_deletes_redis_marker():
    """A passing probe clears the UNHEALTHY marker (auto-recovery)."""
    from llm_gateway.services import health_checker

    redis = _FakeRedis()
    uid = uuid4()
    key = f"llm_gateway:upstream:unhealthy:{uid}"
    redis.store[key] = '{"reason":"http_5xx"}'

    await health_checker._clear_healthy(redis, upstream_id=uid)

    assert key not in redis.store


# --- _run_once end-to-end with quorum + Redis markers ----------------------


async def test_run_once_marks_failing_upstream_and_clears_healthy(monkeypatch):
    """One cycle: one fails, one healthy → marker set on bad, cleared on good."""
    from llm_gateway.services import health_checker

    bad = _FakePersistedUpstream()
    bad.name = "bad-upstream"
    good = _FakePersistedUpstream()
    good.name = "good-upstream"
    active = [bad, good]
    redis = _FakeRedis()

    async def _fake_collect_active_upstreams(session):
        return list(active)

    monkeypatch.setattr(health_checker, "_collect_active_upstreams", _fake_collect_active_upstreams)

    async def _fake_probe_upstream(upstream, *, timeout_seconds):
        if upstream is bad:
            return HealthVerdict(False, 500, "http_5xx")
        return HealthVerdict(True, 200, "ok")

    monkeypatch.setattr(health_checker, "_probe_upstream", _fake_probe_upstream)
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(),
    )
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.record_audit_event",
        _make_fake_record_audit([]),
    )

    await health_checker._run_once(
        redis=redis,
        timeout_seconds=3.0,
        unhealthy_ttl_seconds=30,
        quorum_min=2,
    )

    bad_key = f"llm_gateway:upstream:unhealthy:{bad.id}"
    good_key = f"llm_gateway:upstream:unhealthy:{good.id}"
    assert bad_key in redis.store
    assert good_key not in redis.store


async def test_run_once_treats_404_as_healthy_and_does_not_disable(monkeypatch):
    """404（昇腾 PD 分离）应被视为健康，不标记。"""
    from llm_gateway.services import health_checker

    pd = _FakePersistedUpstream()
    active = [pd]
    redis = _FakeRedis()

    async def _fake_collect_active_upstreams(session):
        return list(active)

    monkeypatch.setattr(health_checker, "_collect_active_upstreams", _fake_collect_active_upstreams)

    async def _fake_probe_upstream(upstream, *, timeout_seconds):
        return HealthVerdict(True, 404, "ok")

    monkeypatch.setattr(health_checker, "_probe_upstream", _fake_probe_upstream)
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(),
    )

    await health_checker._run_once(
        redis=redis,
        timeout_seconds=3.0,
        unhealthy_ttl_seconds=30,
        quorum_min=2,
    )

    pd_key = f"llm_gateway:upstream:unhealthy:{pd.id}"
    assert pd_key not in redis.store


async def test_run_once_continues_when_mark_raises(monkeypatch):
    """单个 _mark_unhealthy 抛异常不能中断对其他节点的处理。

    Three upstreams, two fail (below quorum_min=3 so the batch is applied, not
    suppressed). The first mark raises; the second must still go through.
    """
    from llm_gateway.services import health_checker

    first = _FakePersistedUpstream()
    first.name = "first"
    second = _FakePersistedUpstream()
    second.name = "second"
    healthy = _FakePersistedUpstream()
    healthy.name = "healthy"
    active = [first, second, healthy]
    redis = _FakeRedis()

    async def _fake_collect_active_upstreams(session):
        return list(active)

    monkeypatch.setattr(health_checker, "_collect_active_upstreams", _fake_collect_active_upstreams)

    async def _fake_probe_upstream(upstream, *, timeout_seconds):
        if upstream is healthy:
            return HealthVerdict(True, 200, "ok")
        return HealthVerdict(False, 500, "http_5xx")

    monkeypatch.setattr(health_checker, "_probe_upstream", _fake_probe_upstream)

    real_mark = health_checker._mark_unhealthy
    call_count = {"n": 0}

    async def _flaky_mark(redis_arg, *, upstream_id, upstream_name, verdict, ttl_seconds):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("redis hiccup")
        return await real_mark(
            redis_arg,
            upstream_id=upstream_id,
            upstream_name=upstream_name,
            verdict=verdict,
            ttl_seconds=ttl_seconds,
        )

    monkeypatch.setattr(health_checker, "_mark_unhealthy", _flaky_mark)
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(),
    )
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.record_audit_event",
        _make_fake_record_audit([]),
    )

    # 不应抛异常
    await health_checker._run_once(
        redis=redis,
        timeout_seconds=3.0,
        unhealthy_ttl_seconds=30,
        quorum_min=3,
    )

    # 第一个失败被吞掉，第二个仍被标记
    first_key = f"llm_gateway:upstream:unhealthy:{first.id}"
    second_key = f"llm_gateway:upstream:unhealthy:{second.id}"
    assert first_key not in redis.store
    assert second_key in redis.store


async def test_run_once_noop_when_no_active_upstreams(monkeypatch):
    """没有 ACTIVE upstream → 直接返回，不探测。"""
    from llm_gateway.services import health_checker

    async def _fake_collect_active_upstreams(session):
        return []

    monkeypatch.setattr(health_checker, "_collect_active_upstreams", _fake_collect_active_upstreams)
    probe_calls = []

    async def _fake_probe_upstream(upstream, *, timeout_seconds):
        probe_calls.append(upstream)
        return HealthVerdict(True, 200, "ok")

    monkeypatch.setattr(health_checker, "_probe_upstream", _fake_probe_upstream)
    redis = _FakeRedis()

    await health_checker._run_once(
        redis=redis,
        timeout_seconds=3.0,
        unhealthy_ttl_seconds=30,
        quorum_min=2,
    )
    assert probe_calls == []


# --- Quorum fuse ------------------------------------------------------------


async def test_quorum_breach_suppresses_batch_mark(monkeypatch):
    """≥quorum_min failures in one cycle → skip ALL marking, write one audit.

    The fleet-wide false-positive signature: a frozen event loop makes every
    probe time out in the same tick. The quorum fuse must refuse to apply that
    batch, emitting a single summary audit row instead.
    """
    from llm_gateway.services import health_checker

    a = _FakePersistedUpstream()
    a.name = "a"
    b = _FakePersistedUpstream()
    b.name = "b"
    c = _FakePersistedUpstream()
    c.name = "c"
    active = [a, b, c]
    redis = _FakeRedis()

    async def _fake_collect_active_upstreams(session):
        return list(active)

    monkeypatch.setattr(health_checker, "_collect_active_upstreams", _fake_collect_active_upstreams)

    async def _fake_probe_upstream(upstream, *, timeout_seconds):
        # All three fail — classic checker-side incident
        return HealthVerdict(False, None, "connect_timeout")

    monkeypatch.setattr(health_checker, "_probe_upstream", _fake_probe_upstream)
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(),
    )
    quorum_audits = []
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.record_audit_event",
        _make_fake_record_audit(quorum_audits),
    )

    await health_checker._run_once(
        redis=redis,
        timeout_seconds=3.0,
        unhealthy_ttl_seconds=30,
        quorum_min=2,
    )

    # No markers written — quorum suppressed the batch
    assert redis.store == {}
    # One quorum-failed audit row summarizing the incident
    assert len(quorum_audits) == 1
    assert quorum_audits[0]["action"] == "upstream.health_check_quorum_failed"
    assert quorum_audits[0]["outcome"] == "skipped"
    assert quorum_audits[0]["detail"]["unhealthy_count"] == 3


async def test_quorum_below_threshold_still_marks(monkeypatch):
    """1 failure out of 3 (below quorum_min=2) → still marked, not suppressed."""
    from llm_gateway.services import health_checker

    bad = _FakePersistedUpstream()
    bad.name = "bad"
    good1 = _FakePersistedUpstream()
    good1.name = "good1"
    good2 = _FakePersistedUpstream()
    good2.name = "good2"
    active = [bad, good1, good2]
    redis = _FakeRedis()

    async def _fake_collect_active_upstreams(session):
        return list(active)

    monkeypatch.setattr(health_checker, "_collect_active_upstreams", _fake_collect_active_upstreams)

    async def _fake_probe_upstream(upstream, *, timeout_seconds):
        if upstream is bad:
            return HealthVerdict(False, 500, "http_5xx")
        return HealthVerdict(True, 200, "ok")

    monkeypatch.setattr(health_checker, "_probe_upstream", _fake_probe_upstream)
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(),
    )
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.record_audit_event",
        _make_fake_record_audit([]),
    )

    await health_checker._run_once(
        redis=redis,
        timeout_seconds=3.0,
        unhealthy_ttl_seconds=30,
        quorum_min=2,
    )

    bad_key = f"llm_gateway:upstream:unhealthy:{bad.id}"
    assert bad_key in redis.store


def test_quorum_breach_logic():
    """Direct unit test for the threshold predicate."""
    from llm_gateway.services.health_checker import _quorum_breach

    # 2 of 9 with quorum_min=2 → breach (suspicious batch)
    assert _quorum_breach(2, 9, 2) is True
    # 9 of 9 → breach (fleet-wide — the freeze signature)
    assert _quorum_breach(9, 9, 2) is True
    # 1 of 9 → no breach (single genuine failure)
    assert _quorum_breach(1, 9, 2) is False
    # 1 of 1 → no breach (only one upstream, can't be a "batch")
    assert _quorum_breach(1, 1, 2) is False
    # 0 of 9 → no breach
    assert _quorum_breach(0, 9, 2) is False


# --- Lifecycle (start/stop) ------------------------------------------------


async def test_start_always_starts_loop_skips_probes_when_disabled(monkeypatch):
    """start() always starts the loop; when disabled the loop sleeps without
    probing. This lets an admin re-enable at runtime without restarting.

    The old behavior short-circuited start() when env said disabled, which
    meant a runtime toggle couldn't revive the checker. Now the loop runs
    regardless and re-checks effective_enabled each cycle.
    """
    from llm_gateway.services import health_checker

    # Env default = disabled, no Redis override → effective = disabled
    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: False)
    monkeypatch.setattr(health_checker, "_settings_interval", lambda: 0.01)
    monkeypatch.setattr(health_checker, "_settings_timeout", lambda: 3.0)
    monkeypatch.setattr(health_checker, "_settings_unhealthy_ttl", lambda: 30)
    monkeypatch.setattr(health_checker, "_settings_quorum_min", lambda: 2)
    monkeypatch.setattr(health_checker, "_build_redis", lambda: _FakeRedis())

    probe_calls = []

    async def _fake_run_once(*, redis, timeout_seconds, unhealthy_ttl_seconds, quorum_min):
        probe_calls.append(1)

    monkeypatch.setattr(health_checker, "_run_once", _fake_run_once)

    health_checker._task = None
    await health_checker.start()
    assert health_checker._task is not None  # loop IS running
    await asyncio.sleep(0.05)
    await health_checker.stop()
    assert probe_calls == []  # but no probes happened (disabled)


async def test_start_runs_loop_then_stop_terminates(monkeypatch):
    """start() 起后台 task 并立即返回；stop() 取消并等待其退出。"""
    from llm_gateway.services import health_checker

    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: True)
    monkeypatch.setattr(health_checker, "_settings_interval", lambda: 0.01)
    monkeypatch.setattr(health_checker, "_settings_timeout", lambda: 3.0)
    monkeypatch.setattr(health_checker, "_settings_unhealthy_ttl", lambda: 30)
    monkeypatch.setattr(health_checker, "_settings_quorum_min", lambda: 2)

    iterations = []

    async def _fake_run_once(*, redis, timeout_seconds, unhealthy_ttl_seconds, quorum_min):
        iterations.append(1)

    monkeypatch.setattr(health_checker, "_run_once", _fake_run_once)
    # Avoid a real Redis connection in the unit test
    monkeypatch.setattr(health_checker, "_build_redis", lambda: _FakeRedis())

    health_checker._task = None
    await health_checker.start()
    assert health_checker._task is not None
    await asyncio.sleep(0.05)
    await health_checker.stop()
    assert health_checker._task is None
    assert len(iterations) >= 1


async def test_start_is_idempotent(monkeypatch):
    """重复 start() 在已有 task 时直接返回，不起新 task。"""
    from llm_gateway.services import health_checker

    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: True)
    monkeypatch.setattr(health_checker, "_settings_interval", lambda: 0.01)
    monkeypatch.setattr(health_checker, "_settings_timeout", lambda: 3.0)
    monkeypatch.setattr(health_checker, "_settings_unhealthy_ttl", lambda: 30)
    monkeypatch.setattr(health_checker, "_settings_quorum_min", lambda: 2)
    monkeypatch.setattr(health_checker, "_run_once", _noop_coro_factory())
    monkeypatch.setattr(health_checker, "_build_redis", lambda: _FakeRedis())

    health_checker._task = None
    await health_checker.start()
    first_task = health_checker._task
    await health_checker.start()
    assert health_checker._task is first_task
    await health_checker.stop()


async def test_stop_when_no_task_is_noop(monkeypatch):
    """没有 task 在跑时 stop() 安全返回（幂等）。"""
    from llm_gateway.services import health_checker

    health_checker._task = None
    await health_checker.stop()
    assert health_checker._task is None


def _noop_coro_factory():
    async def _noop(*, redis, timeout_seconds, unhealthy_ttl_seconds, quorum_min):
        return None

    return _noop


def test_classify_health_none_status_and_no_exception_is_unknown_error():
    """M-1 regression guard: classify_health must not raise on (None, None)."""
    verdict = classify_health(None, exc=None)
    assert verdict == HealthVerdict(healthy=False, status_code=None, reason="unknown_error")


@pytest.mark.asyncio(loop_scope="session")
async def test_run_once_integration_marks_redis_and_leaves_pg_active(gateway_fixture, monkeypatch):
    """I-1: integration test exercising the real DB → httpx → Redis → audit path.

    Unlike the unit tests above (which mock _probe/_collect/session/redis), this
    drives the genuine _run_once against a real UpstreamTarget row in the test
    DB and the real redis_client. The httpx leg is intercepted by a MockTransport
    returning 500 so no live vLLM is required. Everything else — the ACTIVE
    candidate SELECT, the Redis marker write, record_audit_event's real row
    insert, and commit — runs for real.

    The defining assertion of the new architecture: PG state stays ACTIVE
    (config state untouched), and the liveness signal lives in Redis instead.
    """
    from sqlalchemy import select

    from llm_gateway.db.models import AuditEvent, ResourceState, UpstreamTarget
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services import health_checker
    from llm_gateway.services.rate_limit import redis_client
    from llm_gateway.services.upstream_health import _key

    # Repoint the existing gateway_fixture upstream at a stub that returns 500.
    async with AsyncSessionLocal() as session:
        upstream = await session.get(UpstreamTarget, gateway_fixture.upstream_id)
        upstream.base_url = "http://health-check-stub.local/v1"
        await session.commit()

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream error")

    real_async_client = health_checker.httpx.AsyncClient

    class _StubClient(real_async_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(health_checker.httpx, "AsyncClient", _StubClient)

    # Clean any stale marker from a prior run before asserting.
    marker_key = _key(gateway_fixture.upstream_id)
    await redis_client.delete(marker_key)

    # The test DB accumulates upstream rows from other tests' gateway_fixture
    # instances (session-scoped, no per-test teardown). Those point at real or
    # unreachable URLs and will also fail their probes. To keep this test about
    # the gateway_fixture upstream only — and avoid the quorum fuse suppressing
    # the marker because a dozen unrelated upstreams also failed — collect only
    # the fixture's upstream.
    fixture_upstream_id = gateway_fixture.upstream_id

    async def _collect_fixture_upstream(session):
        row = await session.get(UpstreamTarget, fixture_upstream_id)
        return [row] if row is not None else []

    monkeypatch.setattr(health_checker, "_collect_active_upstreams", _collect_fixture_upstream)

    # 1 of 1 is below quorum_min=2, so the marker IS applied (single genuine
    # failure, not a batch).
    await health_checker._run_once(
        redis=redis_client,
        timeout_seconds=3.0,
        unhealthy_ttl_seconds=30,
        quorum_min=2,
    )

    # Redis marker present — runtime liveness signal lives here now.
    assert await redis_client.exists(marker_key)

    # PG state UNCHANGED — config state is admin-owned, the checker must not
    # touch it. This is the core of the config/runtime state separation.
    async with AsyncSessionLocal() as session:
        upstream = await session.get(UpstreamTarget, gateway_fixture.upstream_id)
        assert upstream.state == ResourceState.ACTIVE

        audit_rows = (
            (
                await session.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.resource_id == gateway_fixture.upstream_id,
                        AuditEvent.action == "upstream.auto_disable",
                    )
                    .order_by(AuditEvent.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        assert audit_rows, "expected an upstream.auto_disable audit row"
        latest = audit_rows[0]
        assert latest.outcome == "unhealthy"
        assert latest.detail.get("verdict") == "http_5xx"
        assert latest.detail.get("status_code") == 500
        assert latest.actor_subject_id is None  # automatic, no human actor

    # Cleanup: remove the marker so it doesn't leak into other tests.
    await redis_client.delete(marker_key)
