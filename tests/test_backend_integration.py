from __future__ import annotations

from uuid import uuid4

import pytest

from llm_gateway.db.models import EndpointFamily, RequestOutcome, UsageSource

from conftest import fetch_request_fact


pytestmark = pytest.mark.asyncio(loop_scope="session")


def _auth_headers(raw_key: str, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {raw_key}"}
    if request_id:
        headers["x-request-id"] = request_id
    return headers


async def test_list_models_returns_entitled_aliases(client, gateway_fixture):
    response = await client.get(
        "/v1/models",
        headers=_auth_headers(gateway_fixture.raw_key),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    ids = [m["id"] for m in payload["data"]]
    assert gateway_fixture.model_alias in ids
    for m in payload["data"]:
        assert m["object"] == "model"
        assert "created" in m
        assert m["owned_by"] == "gateway"


async def test_list_models_rejects_invalid_key(client):
    response = await client.get(
        "/v1/models",
        headers={"Authorization": "Bearer gw-invalid"},
    )
    assert response.status_code == 401


async def test_health_and_admin_diagnostics(client):
    from llm_gateway.core.config import get_settings

    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["ok"] is True

    diagnostics = await client.get("/admin/diagnostics", headers={"x-admin-token": get_settings().admin_token})
    assert diagnostics.status_code == 200
    assert diagnostics.json()["litellm_version"] != "unknown"


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


async def test_anthropic_messages_conversion_uses_litellm_and_records_usage(client, gateway_fixture):
    request_id = f"pytest-anthropic-{uuid4()}"
    response = await client.post(
        "/v1/messages",
        headers={"x-api-key": gateway_fixture.raw_key, "x-request-id": request_id},
        json={
            "model": gateway_fixture.model_alias,
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "Reply with exactly one short sentence."}],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("content")
    assert payload.get("usage", {}).get("input_tokens", 0) > 0

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.ANTHROPIC_MESSAGES
    assert fact.outcome == RequestOutcome.SUCCESS
    assert fact.usage_source == UsageSource.LITELLM
    assert fact.prompt_tokens and fact.prompt_tokens > 0


async def test_model_ip_allowlist_denies_disallowed_client(external_ip_client, gateway_fixture):
    from llm_gateway.db.models import IPPolicyMode, ModelAlias
    from llm_gateway.db.session import AsyncSessionLocal

    request_id = f"pytest-ip-deny-{uuid4()}"
    async with AsyncSessionLocal() as session:
        model_alias = await session.get(ModelAlias, gateway_fixture.model_alias_id)
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
            "messages": [{"role": "user", "content": "This should be rate limited before upstream."}],
            "max_tokens": 16,
        },
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "request_rate_exceeded"

    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.RATE_LIMITED


async def test_admin_updates_router_command_rate_policy_and_upstream_health(client, gateway_fixture):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}

    health = await client.get(f"/admin/upstreams/{gateway_fixture.upstream_id}/health", headers=headers)
    assert health.status_code == 200, health.text
    health_payload = health.json()
    assert health_payload["upstream"]["api_key_value"] is None
    assert health_payload["upstream"]["has_api_key"] is True
    assert health_payload["health"]["status_code"] < 500

    alias_patch = await client.patch(
        f"/admin/model-aliases/{gateway_fixture.model_alias_id}",
        headers=headers,
        json={"notes": "updated by integration test"},
    )
    assert alias_patch.status_code == 200, alias_patch.text
    assert alias_patch.json()["notes"] == "updated by integration test"

    router_config = await client.post(
        "/admin/router-command-configs",
        headers=headers,
        json={
            "model_alias_id": str(gateway_fixture.model_alias_id),
            "name": "pytest-router",
            "worker_urls": ["http://127.0.0.1:9001", "http://127.0.0.1:9002"],
            "policy": "consistent_hash",
            "port": 19001,
            "extra_args": {"request_timeout": 30},
        },
    )
    assert router_config.status_code == 200, router_config.text
    command = router_config.json()["command"]
    assert "vllm-router" in command
    assert "--worker-urls" in command
    assert "http://127.0.0.1:9001" in command

    rate_policy = await client.post(
        "/admin/rate-policies",
        headers=headers,
        json={
            "scope": "project",
            "scope_id": str(gateway_fixture.project_id),
            "requests_per_minute": 99,
            "concurrency_limit": 7,
        },
    )
    assert rate_policy.status_code == 200, rate_policy.text
    policy_id = rate_policy.json()["id"]

    patched_policy = await client.patch(
        f"/admin/rate-policies/{policy_id}",
        headers=headers,
        json={"requests_per_minute": 55},
    )
    assert patched_policy.status_code == 200, patched_policy.text
    assert patched_policy.json()["requests_per_minute"] == 55
