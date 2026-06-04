from __future__ import annotations

from typing import Any
from uuid import uuid4

import litellm
import pytest

from llm_gateway.db.models import EndpointFamily, ModelAlias, UpstreamTarget
from llm_gateway.services import litellm_client
from llm_gateway.services.litellm_client import LiteLLMCallResult


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_litellm_routing_config_forces_openai_messages_to_chat_completions():
    assert litellm.use_chat_completions_url_for_anthropic_messages is True


async def test_anthropic_messages_once_sets_drop_params(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_anthropic_messages(**kwargs):
        captured.update(kwargs)
        return {"usage": {"input_tokens": 3, "output_tokens": 2}}

    monkeypatch.setattr(litellm_client, "anthropic_messages", fake_anthropic_messages)

    result = await litellm_client.anthropic_messages_once(
        model_alias=_model_alias(),
        upstream=_upstream(),
        body={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
    )

    assert captured["drop_params"] is True
    assert captured["model"] == "openai/test-model"
    assert result.usage == {"input_tokens": 3, "output_tokens": 2}


async def test_responses_once_routes_openai_compatible_upstreams_to_chat_completions(
    monkeypatch,
):
    captured: dict[str, Any] = {}

    async def fake_aresponses(**kwargs):
        captured.update(kwargs)
        return {"usage": {"input_tokens": 9, "output_tokens": 1}}

    monkeypatch.setattr(litellm_client, "aresponses", fake_aresponses)

    result = await litellm_client.responses_once(
        model_alias=_model_alias(),
        upstream=_upstream(),
        body={"input": "hi", "max_output_tokens": 8},
    )

    assert captured["model"] == "openai/test-model"
    assert captured["use_chat_completions_api"] is True
    assert result.usage == {"input_tokens": 9, "output_tokens": 1}


async def test_responses_once_leaves_anthropic_prefix_on_provider_route(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_aresponses(**kwargs):
        captured.update(kwargs)
        return {"usage": {"input_tokens": 5, "output_tokens": 2}}

    monkeypatch.setattr(litellm_client, "aresponses", fake_aresponses)

    result = await litellm_client.responses_once(
        model_alias=_model_alias(litellm_model="anthropic/claude-test"),
        upstream=_upstream(),
        body={"input": "hi", "max_output_tokens": 8},
    )

    assert captured["model"] == "anthropic/claude-test"
    assert "use_chat_completions_api" not in captured
    assert result.usage == {"input_tokens": 5, "output_tokens": 2}


async def test_unified_upstream_request_dispatches_by_endpoint_family(monkeypatch):
    seen: list[EndpointFamily] = []

    async def fake_completion_once(*, model_alias, upstream, body):
        seen.append(EndpointFamily.OPENAI_CHAT)
        return LiteLLMCallResult(response={"kind": "chat"}, usage=None)

    async def fake_responses_once(*, model_alias, upstream, body):
        seen.append(EndpointFamily.OPENAI_RESPONSES)
        return LiteLLMCallResult(response={"kind": "responses"}, usage=None)

    async def fake_anthropic_messages_once(*, model_alias, upstream, body):
        seen.append(EndpointFamily.ANTHROPIC_MESSAGES)
        return LiteLLMCallResult(response={"kind": "messages"}, usage=None)

    monkeypatch.setattr(litellm_client, "completion_once", fake_completion_once)
    monkeypatch.setattr(litellm_client, "responses_once", fake_responses_once)
    monkeypatch.setattr(
        litellm_client, "anthropic_messages_once", fake_anthropic_messages_once
    )

    for endpoint_family in (
        EndpointFamily.OPENAI_CHAT,
        EndpointFamily.OPENAI_RESPONSES,
        EndpointFamily.ANTHROPIC_MESSAGES,
    ):
        await litellm_client.upstream_request_once(
            endpoint_family=endpoint_family,
            model_alias=_model_alias(),
            upstream=_upstream(),
            body={},
        )

    assert seen == [
        EndpointFamily.OPENAI_CHAT,
        EndpointFamily.OPENAI_RESPONSES,
        EndpointFamily.ANTHROPIC_MESSAGES,
    ]


async def test_anthropic_messages_stream_sets_drop_params(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_anthropic_messages(**kwargs):
        captured.update(kwargs)

        async def stream():
            yield {"usage": {"input_tokens": 4, "output_tokens": 6}}

        return stream()

    monkeypatch.setattr(litellm_client, "anthropic_messages", fake_anthropic_messages)

    chunks = [
        item
        async for item in litellm_client.anthropic_messages_stream(
            model_alias=_model_alias(),
            upstream=_upstream(),
            body={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
        )
    ]

    assert captured["drop_params"] is True
    assert captured["stream"] is True
    assert chunks[0][1] == {"input_tokens": 4, "output_tokens": 6}


def _model_alias(litellm_model: str = "openai/test-model") -> ModelAlias:
    return ModelAlias(
        alias="test-model",
        upstream_model_name="test-model",
        litellm_model=litellm_model,
    )


def _upstream() -> UpstreamTarget:
    return UpstreamTarget(
        model_alias_id=uuid4(),
        name="test-upstream",
        base_url="http://127.0.0.1:65530/v1",
        api_key_value="sk-test",
    )
