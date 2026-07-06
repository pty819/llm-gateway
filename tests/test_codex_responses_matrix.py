from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import pytest

from llm_gateway.db.models import EndpointFamily, RequestOutcome, UsageSource
from llm_gateway.services.upstream_client import UpstreamCallResult as LiteLLMCallResult

from conftest import fetch_request_fact


pytestmark = pytest.mark.asyncio(loop_scope="session")


def _auth_headers(raw_key: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}", "x-request-id": request_id}


def _codex_responses_body(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Use the inspect_workspace tool if it is required.",
                    }
                ],
            }
        ],
        "tools": [
            {
                "type": "function",
                "name": "inspect_workspace",
                "description": "Inspect a file in the local workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            }
        ],
        "reasoning": {"effort": "medium"},
        "max_output_tokens": 64,
    }


async def test_codex_responses_tools_reasoning_and_usage_are_passed_through(
    client, gateway_fixture, monkeypatch
):
    seen: dict[str, Any] = {}

    async def fake_upstream_request_once(
        *, endpoint_family, model_alias, upstream, body
    ):
        assert endpoint_family == EndpointFamily.OPENAI_RESPONSES
        seen["model_alias"] = model_alias.alias
        seen["base_url"] = upstream.base_url
        seen["body"] = body
        return LiteLLMCallResult(
            response={
                "id": "resp_test",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
                "usage": {"input_tokens": 21, "output_tokens": 7, "total_tokens": 28},
            },
            usage={
                "input_tokens": 21,
                "output_tokens": 7,
                "total_tokens": 28,
                "input_tokens_details": {"cached_tokens": 9},
                "performance": {"queue_ms": 3, "prefill_ms": 11, "decode_ms": 17},
                "kv_cache_usage": 0.51,
            },
        )

    monkeypatch.setattr(
        "llm_gateway.api.proxy.upstream_request_once", fake_upstream_request_once
    )

    request_id = f"pytest-codex-matrix-{uuid4()}"
    response = await client.post(
        "/v1/responses",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json=_codex_responses_body(gateway_fixture.model_alias),
    )

    assert response.status_code == 200, response.text
    assert seen["model_alias"] == gateway_fixture.model_alias
    assert seen["body"]["tools"][0]["name"] == "inspect_workspace"
    assert seen["body"]["reasoning"]["effort"] == "medium"
    assert seen["body"]["input"][0]["content"][0]["type"] == "input_text"

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.OPENAI_RESPONSES
    assert fact.outcome == RequestOutcome.SUCCESS
    assert fact.usage_source == UsageSource.LITELLM
    assert fact.prompt_tokens == 21
    assert fact.completion_tokens == 7
    assert fact.cached_tokens == 9
    assert fact.latency_ms is not None
    assert fact.queue_ms == 3
    assert fact.prefill_ms == 11
    assert fact.decode_ms == 17
    assert fact.kv_cache_usage == 0.51


async def test_codex_responses_stream_records_ttft_and_stream_duration(
    client, gateway_fixture, monkeypatch
):
    async def fake_upstream_request_stream(
        *, endpoint_family, model_alias, upstream, body
    ) -> AsyncGenerator[tuple[str, dict[str, Any] | None], None]:
        assert endpoint_family == EndpointFamily.OPENAI_RESPONSES
        assert body["stream"] is True
        yield 'data: {"type":"response.created"}\n\n', None
        yield (
            'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
            {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
        )
        yield "data: [DONE]\n\n", None

    monkeypatch.setattr(
        "llm_gateway.api.proxy.upstream_request_stream", fake_upstream_request_stream
    )

    request_id = f"pytest-codex-stream-{uuid4()}"
    body = _codex_responses_body(gateway_fixture.model_alias)
    body["stream"] = True
    async with client.stream(
        "POST",
        "/v1/responses",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json=body,
    ) as response:
        payload = await response.aread()

    assert response.status_code == 200
    assert b"response.output_text.delta" in payload
    assert b"[DONE]" in payload

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.OPENAI_RESPONSES
    assert fact.outcome == RequestOutcome.SUCCESS
    assert fact.streaming is True
    assert fact.time_to_first_token_ms is not None
    assert fact.stream_duration_ms is not None
    assert fact.total_tokens == 10


async def test_codex_responses_long_context_shape_is_forwarded(
    client, gateway_fixture, monkeypatch
):
    async def fake_upstream_request_once(
        *, endpoint_family, model_alias, upstream, body
    ):
        assert endpoint_family == EndpointFamily.OPENAI_RESPONSES
        text = body["input"][0]["content"][0]["text"]
        assert "line-199" in text
        return LiteLLMCallResult(
            response={"id": "resp_long", "object": "response", "status": "completed"},
            usage={"input_tokens": 1000, "output_tokens": 1, "total_tokens": 1001},
        )

    monkeypatch.setattr(
        "llm_gateway.api.proxy.upstream_request_once", fake_upstream_request_once
    )

    request_id = f"pytest-codex-long-{uuid4()}"
    body = _codex_responses_body(gateway_fixture.model_alias)
    body["input"][0]["content"][0]["text"] = "\n".join(
        f"line-{index}: keep this context stable" for index in range(200)
    )
    response = await client.post(
        "/v1/responses",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json=body,
    )

    assert response.status_code == 200, response.text
    fact = await fetch_request_fact(request_id)
    assert fact.total_tokens == 1001


async def test_codex_responses_adapter_error_records_failure(
    client, gateway_fixture, monkeypatch
):
    async def fake_upstream_request_once(
        *, endpoint_family, model_alias, upstream, body
    ):
        assert endpoint_family == EndpointFamily.OPENAI_RESPONSES
        raise RuntimeError("synthetic adapter failure")

    monkeypatch.setattr(
        "llm_gateway.api.proxy.upstream_request_once", fake_upstream_request_once
    )

    request_id = f"pytest-codex-error-{uuid4()}"
    response = await client.post(
        "/v1/responses",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json=_codex_responses_body(gateway_fixture.model_alias),
    )

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "adapter_failure"

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.OPENAI_RESPONSES
    assert fact.outcome == RequestOutcome.ADAPTER_FAILURE
    assert fact.error_class == "RuntimeError"
