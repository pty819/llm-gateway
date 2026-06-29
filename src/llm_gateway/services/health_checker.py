from __future__ import annotations

from dataclasses import dataclass

import httpx


HEALTHY_STATUSES = frozenset({200, 404})


@dataclass(frozen=True)
class HealthVerdict:
    healthy: bool
    status_code: int | None
    reason: str


def classify_health(
    status_code: int | None, *, exc: Exception | None
) -> HealthVerdict:
    """Classify an upstream /models probe into a health verdict.

    200/404 are healthy (404 = 昇腾 PD 分离查不到 /models，明确是健康的).
    Any 5xx, network error, timeout, or non-404 4xx is unhealthy and triggers
    automatic disable.
    """
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
            return HealthVerdict(False, None, "timeout")
        if isinstance(exc, httpx.HTTPError):
            return HealthVerdict(False, None, "connection_error")
        return HealthVerdict(False, None, "unknown_error")
    if status_code in HEALTHY_STATUSES:
        return HealthVerdict(True, status_code, "ok")
    if status_code >= 500:
        return HealthVerdict(False, status_code, "http_5xx")
    return HealthVerdict(False, status_code, "unexpected_status")


async def _probe_upstream(upstream, *, timeout_seconds: float) -> HealthVerdict:
    """GET {base_url}/{health_path} and classify the response.

    Mirrors the request construction of litellm_client.check_upstream_health
    (same base_url join, same header injection) but applies the stricter
    classify_health verdict used by the background checker.
    """
    # base_url 形如 "http://host:port/v1"，health_path 形如 "/models"。
    # 用字符串拼接保留与 check_upstream_health 完全一致的 URL 形态。
    url = upstream.base_url.rstrip("/") + "/" + upstream.health_path.lstrip("/")
    headers = dict(upstream.extra_headers or {})
    api_key = upstream.api_key_value or upstream.api_key_ref
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, headers=headers)
        return classify_health(response.status_code, exc=None)
    except Exception as exc:
        return classify_health(None, exc=exc)
