from __future__ import annotations

import httpx
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
        (None, httpx.ConnectTimeout("x"), False, "timeout"),
        (None, httpx.ReadTimeout("x"), False, "timeout"),
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


async def test_probe_upstream_returns_timeout_verdict(monkeypatch):
    from llm_gateway.services import health_checker

    upstream = _FakeUpstream(base_url="http://upstream.local", health_path="/models")
    monkeypatch.setattr(
        health_checker.httpx,
        "AsyncClient",
        _make_fake_client(exc=httpx.ConnectTimeout("timed out")),
    )

    verdict = await health_checker._probe_upstream(upstream, timeout_seconds=3.0)
    assert verdict == HealthVerdict(healthy=False, status_code=None, reason="timeout")


async def test_probe_upstream_injects_authorization_header(monkeypatch):
    """api_key_value/ref 必须以 Bearer 注入，复用 litellm_client._api_key 语义。"""
    from llm_gateway.services import health_checker

    upstream = _FakeUpstream(
        base_url="http://upstream.local", api_key_value="secret-key"
    )
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


class _FakePersistedUpstream:
    """Stand-in for an UpstreamTarget row loaded from DB.

    Carries the fields _disable_upstream reads/writes after session.get(): the
    identity + name + health_path for the audit detail, and `state` which the
    function sets to DISABLED. _committed records whether commit() ran.
    """

    def __init__(self, *, state):
        from uuid import uuid4

        self.id = uuid4()
        self.name = "fake-upstream"
        self.health_path = "/models"
        self.state = state
        self._committed = False


def _make_fake_session_local(upstream):
    """Build a fake AsyncSessionLocal context manager that returns `upstream`
    from .get() and flips upstream._committed on .commit()."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_session_local():
        class _Session:
            async def get(self_inner, model, pk):
                return upstream

            async def commit(self_inner):
                upstream._committed = True

        yield _Session()

    return _fake_session_local


def _make_fake_record_audit(recorded):
    async def _fake_record(session, **kwargs):
        recorded.append(kwargs)
        return None

    return _fake_record


async def test_disable_upstream_sets_state_and_writes_audit(monkeypatch):
    """探测失败 → 双重确认仍 ACTIVE → 写 DISABLED + audit event。"""
    from llm_gateway.db.models import ResourceState
    from llm_gateway.services import health_checker

    upstream = _FakePersistedUpstream(state=ResourceState.ACTIVE)
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(upstream),
    )
    recorded = []
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.record_audit_event",
        _make_fake_record_audit(recorded),
    )

    verdict = HealthVerdict(False, 500, "http_5xx")
    disabled = await health_checker._disable_upstream(
        upstream_id=upstream.id, verdict=verdict
    )

    assert disabled is True
    assert upstream.state == ResourceState.DISABLED
    assert upstream._committed is True
    assert len(recorded) == 1
    assert recorded[0]["action"] == "upstream.auto_disable"
    assert recorded[0]["resource_id"] == upstream.id
    assert recorded[0]["outcome"] == "disabled"
    assert recorded[0]["detail"]["verdict"] == "http_5xx"
    assert recorded[0]["detail"]["status_code"] == 500


async def test_disable_upstream_skips_when_already_disabled(monkeypatch):
    """双重确认：探测后管理员已手动禁用 → 不重复写 audit。"""
    from llm_gateway.db.models import ResourceState
    from llm_gateway.services import health_checker

    upstream = _FakePersistedUpstream(state=ResourceState.DISABLED)
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(upstream),
    )
    recorded = []
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.record_audit_event",
        _make_fake_record_audit(recorded),
    )

    verdict = HealthVerdict(False, 500, "http_5xx")
    disabled = await health_checker._disable_upstream(
        upstream_id=upstream.id, verdict=verdict
    )

    assert disabled is False
    assert upstream._committed is False
    assert len(recorded) == 0  # 不重复审计
