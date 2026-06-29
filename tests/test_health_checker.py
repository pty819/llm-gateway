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
