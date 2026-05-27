from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from llm_gateway.db.models import ModelAlias, UpstreamTarget
from llm_gateway.services import litellm_client


pytestmark = pytest.mark.asyncio(loop_scope="session")


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


def _model_alias() -> ModelAlias:
    return ModelAlias(
        alias="test-model",
        upstream_model_name="test-model",
        litellm_model="openai/test-model",
    )


def _upstream() -> UpstreamTarget:
    return UpstreamTarget(
        model_alias_id=uuid4(),
        name="test-upstream",
        base_url="http://127.0.0.1:65530/v1",
        api_key_value="sk-test",
    )
