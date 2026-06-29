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
