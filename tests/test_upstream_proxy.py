from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from conftest import fetch_request_fact

from llm_gateway.db.models import (
    EndpointFamily,
    RequestOutcome,
    UsageSource,
)
from llm_gateway.services.upstream_client import UpstreamCallResult as LiteLLMCallResult
from tests.helpers import _auth_headers

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_openai_chat_completion_uses_real_upstream_and_records_usage(client, gateway_fixture):
    request_id = f"pytest-openai-{uuid4()}"
    response = await client.post(
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [{"role": "user", "content": "Reply with exactly one short sentence."}],
            "max_tokens": 32,
            "temperature": 0,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["choices"]
    assert payload.get("usage", {}).get("total_tokens", 0) > 0

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.OPENAI_CHAT
    assert fact.outcome == RequestOutcome.SUCCESS
    assert fact.usage_source == UsageSource.LITELLM
    assert fact.total_tokens and fact.total_tokens > 0


async def test_invalid_gateway_key_records_auth_failure(client, gateway_fixture):
    request_id = f"pytest-auth-failure-{uuid4()}"
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer gw-invalid", "x-request-id": request_id},
        json={
            "model": gateway_fixture.model_alias,
            "messages": [{"role": "user", "content": "This should not reach upstream."}],
            "max_tokens": 16,
        },
    )

    assert response.status_code == 401

    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.AUTH_FAILURE
    assert fact.subject_id is None
    assert fact.upstream_target_id is None


async def test_openai_stream_completion_records_success(client, gateway_fixture):
    request_id = f"pytest-openai-stream-{uuid4()}"
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [{"role": "user", "content": "Say stream-ok in a short sentence."}],
            "max_tokens": 32,
            "temperature": 0,
            "stream": True,
        },
    ) as response:
        body = await response.aread()

    assert response.status_code == 200
    assert b"data:" in body
    assert b"[DONE]" in body

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.OPENAI_CHAT
    assert fact.outcome == RequestOutcome.SUCCESS
    assert fact.streaming is True


async def test_model_ip_allowlist_denies_disallowed_client(external_ip_client, gateway_fixture):
    from llm_gateway.db.models import IPPolicyMode, ModelAlias
    from llm_gateway.db.session import AsyncSessionLocal

    request_id = f"pytest-ip-deny-{uuid4()}"
    async with AsyncSessionLocal() as session:
        model_alias = await session.get(ModelAlias, gateway_fixture.model_alias_id)
        assert model_alias is not None
        model_alias.ip_policy_mode = IPPolicyMode.ALLOWLIST
        model_alias.ip_allowlist_cidrs = ["203.0.113.1/32"]
        await session.commit()

    response = await external_ip_client.post(
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [{"role": "user", "content": "This should be denied before upstream."}],
            "max_tokens": 16,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "model_ip_denied"

    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.POLICY_DENIAL
    assert fact.upstream_target_id is None


async def test_model_ip_allowlist_accepts_forwarded_client_from_trusted_vite_proxy(
    client, gateway_fixture, monkeypatch
):
    from llm_gateway.api.deps import settings_dep
    from llm_gateway.core.config import Settings
    from llm_gateway.db.models import IPPolicyMode, ModelAlias
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.main import app

    async def fake_upstream_request_once(*, endpoint_family, model_alias, upstream, body):
        assert endpoint_family == EndpointFamily.OPENAI_CHAT
        return LiteLLMCallResult(
            response={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
            usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        )

    monkeypatch.setattr("llm_gateway.api.proxy.upstream_request_once", fake_upstream_request_once)

    def trusted_proxy_settings() -> Settings:
        settings = Settings()
        settings.trusted_proxy_headers = True
        settings.trusted_proxy_cidrs = "127.0.0.0/8,::1/128"
        return settings

    app.dependency_overrides[settings_dep] = trusted_proxy_settings

    request_id = f"pytest-ip-forwarded-allow-{uuid4()}"
    async with AsyncSessionLocal() as session:
        model_alias = await session.get(ModelAlias, gateway_fixture.model_alias_id)
        assert model_alias is not None
        model_alias.ip_policy_mode = IPPolicyMode.ALLOWLIST
        model_alias.ip_allowlist_cidrs = ["10.21.48.65/32"]
        await session.commit()

    try:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                **_auth_headers(gateway_fixture.raw_key, request_id),
                "x-forwarded-for": "10.21.48.65, 127.0.0.1",
            },
            json={
                "model": gateway_fixture.model_alias,
                "messages": [{"role": "user", "content": "This should pass."}],
                "max_tokens": 16,
            },
        )
    finally:
        app.dependency_overrides.pop(settings_dep, None)

    assert response.status_code == 200, response.text
    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.SUCCESS


async def test_openai_chat_completion_records_realtime_runtime_metrics(
    client, gateway_fixture, monkeypatch
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.services.rate_limit import redis_client
    from llm_gateway.services.runtime_metrics import ACTIVE_KEY, runtime_snapshot

    async def no_metric_targets(redis=None):
        return []

    monkeypatch.setattr("llm_gateway.api.realtime._load_vllm_metric_targets", no_metric_targets)

    observed_during_call: dict[str, object] = {}

    async def fake_upstream_request_once(*, endpoint_family, model_alias, upstream, body):
        assert endpoint_family == EndpointFamily.OPENAI_CHAT
        observed_during_call.update(await runtime_snapshot(redis_client))
        return LiteLLMCallResult(
            response={
                "id": "chatcmpl-runtime-metrics",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
            usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        )

    await redis_client.delete(ACTIVE_KEY)
    monkeypatch.setattr("llm_gateway.api.proxy.upstream_request_once", fake_upstream_request_once)

    request_id = f"pytest-runtime-metrics-{uuid4()}"
    response = await client.post(
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [{"role": "user", "content": "metrics"}],
            "max_tokens": 16,
        },
    )
    assert response.status_code == 200, response.text
    assert observed_during_call["active_connections"] == 1
    active_rows = cast(list[dict[str, Any]], observed_during_call["upstreams"])
    assert any(item["upstream_id"] == str(gateway_fixture.upstream_id) for item in active_rows)

    snapshot = await client.get(
        "/admin/realtime/snapshot",
        headers={"x-admin-token": get_settings().admin_token},
        params={"window_seconds": 60},
    )
    assert snapshot.status_code == 200, snapshot.text
    payload = snapshot.json()
    assert payload["total_recent_tokens"] is None
    assert payload["total_tokens_per_second"] is None
    assert payload["active_connections"] == 0
    assert all(
        item["upstream_id"] != str(gateway_fixture.upstream_id) for item in payload["upstreams"]
    )


async def test_realtime_snapshot_includes_cached_vllm_metrics(client, gateway_fixture, monkeypatch):
    from llm_gateway.core.config import get_settings
    from llm_gateway.services.rate_limit import redis_client
    from llm_gateway.services.runtime_metrics import (
        VLLM_METRICS_CACHE_PREFIX,
        VLLM_METRICS_COUNTER_PREFIX,
        VLLM_METRICS_LOCK_PREFIX,
        VLLMMetricsTarget,
    )

    async def metric_targets(redis=None):
        return [
            VLLMMetricsTarget(
                upstream_id=str(gateway_fixture.upstream_id),
                upstream_name="pytest-vllm",
                model_alias=gateway_fixture.model_alias,
                base_url="http://pytest-vllm:8000/v1",
                extra_headers={},
            )
        ]

    async def fake_metrics_text(target):
        assert target.upstream_id == str(gateway_fixture.upstream_id)
        return """
vllm:num_requests_running 2
vllm:num_requests_waiting 1
vllm:kv_cache_usage_perc 0.8
vllm:prefix_cache_queries 20
vllm:prefix_cache_hits 15
vllm:prompt_tokens_total 200
vllm:generation_tokens_total 100
"""

    await redis_client.delete(
        f"{VLLM_METRICS_CACHE_PREFIX}:{gateway_fixture.upstream_id}",
        f"{VLLM_METRICS_LOCK_PREFIX}:{gateway_fixture.upstream_id}",
        f"{VLLM_METRICS_COUNTER_PREFIX}:{gateway_fixture.upstream_id}",
    )
    monkeypatch.setattr("llm_gateway.api.realtime._load_vllm_metric_targets", metric_targets)
    monkeypatch.setattr(
        "llm_gateway.services.runtime_metrics._fetch_vllm_metrics_text",
        fake_metrics_text,
    )

    response = await client.get(
        "/admin/realtime/snapshot",
        headers={"x-admin-token": get_settings().admin_token},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["vllm"]["observed_upstreams"] == 1
    assert payload["vllm"]["ok_upstreams"] == 1
    assert payload["vllm"]["running"] == 2
    assert payload["vllm"]["waiting"] == 1
    assert payload["vllm"]["max_kv_cache_usage"] == 0.8
    assert payload["upstreams"][0]["vllm"]["kind"] == "vllm"
    assert payload["upstreams"][0]["vllm"]["prefix_cache_hit_ratio"] == 0.75


async def test_load_vllm_metric_targets_materializes_before_session_close():
    from llm_gateway.api.realtime import _load_vllm_metric_targets
    from llm_gateway.db.models import ModelAlias, UpstreamTarget
    from llm_gateway.db.session import AsyncSessionLocal

    suffix = uuid4().hex
    model_alias = f"pytest-realtime-materialize-{suffix}"
    upstream_name = f"pytest-realtime-upstream-{suffix}"
    base_url = "http://pytest-vllm:8000/v1"
    metrics_url = "http://pytest-vllm:29000/metrics"
    api_key = "sk-pytest-realtime"
    extra_headers = {"X-Pytest-Realtime": "1"}

    async with AsyncSessionLocal() as session:
        model = ModelAlias(
            alias=model_alias,
            upstream_model_name="pytest-upstream-model",
            litellm_model="pytest-upstream-model",
        )
        session.add(model)
        await session.flush()

        upstream = UpstreamTarget(
            model_alias_id=model.id,
            name=upstream_name,
            base_url=base_url,
            metrics_url=metrics_url,
            api_key_value=api_key,
            extra_headers=extra_headers,
        )
        session.add(upstream)
        await session.flush()
        upstream_id = str(upstream.id)
        await session.commit()

    targets = await _load_vllm_metric_targets(redis=None)
    target = next(item for item in targets if item.upstream_id == upstream_id)

    assert target.upstream_name == upstream_name
    assert target.model_alias == model_alias
    assert target.base_url == base_url
    assert target.metrics_url == metrics_url
    assert target.api_key == api_key
    assert target.extra_headers == extra_headers


async def test_key_scoped_rate_policy_blocks_before_upstream(client, gateway_fixture):
    from llm_gateway.db.models import RatePolicy
    from llm_gateway.db.session import AsyncSessionLocal

    request_id = f"pytest-rate-deny-{uuid4()}"
    async with AsyncSessionLocal() as session:
        session.add(
            RatePolicy(
                scope="key",
                scope_id=gateway_fixture.key_id,
                requests_per_minute=0,
                concurrency_limit=1,
            )
        )
        await session.commit()

    response = await client.post(
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [
                {
                    "role": "user",
                    "content": "This should be rate limited before upstream.",
                }
            ],
            "max_tokens": 16,
        },
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "request_rate_exceeded"

    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.RATE_LIMITED
