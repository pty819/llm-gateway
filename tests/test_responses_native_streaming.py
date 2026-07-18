"""Regression test for the /v1/responses streaming failure.

Root cause: LiteLLM's OpenAIResponsesAPIConfig.should_fake_stream() consults
the LiteLLM model registry for `supports_native_streaming`. Custom vLLM model
names (qwen3, Qwen2.5-72B-Instruct, etc.) are NOT in the registry, so the
lookup raises and should_fake_stream returns True. LiteLLM then drops
`stream=True` from the upstream /responses request, POSTs a non-streaming
call to vLLM, and slices the full JSON into fake SSE chunks. Against vLLM's
Responses endpoint this produces "stream disconnected before completion" /
"error sending for url", while Codex hitting vLLM directly (real stream=True)
works fine.

Fix: register_model_for_native_streaming() upserts a registry entry with
supports_native_streaming=True for each ModelAlias, so LiteLLM sends real
stream=True through to vLLM.
"""

from __future__ import annotations

from llm_gateway.db.models import ModelAlias
from llm_gateway.services.litellm_client import (
    register_model_for_native_streaming,
)


def _should_fake_stream(litellm_model: str) -> bool:
    """Probe LiteLLM's real fake-stream decision for a model string."""
    from litellm.llms.openai.responses.transformation import OpenAIResponsesAPIConfig

    cfg = OpenAIResponsesAPIConfig()
    return cfg.should_fake_stream(
        model=litellm_model, stream=True, custom_llm_provider="openai"
    )


def _make_alias(litellm_model: str) -> ModelAlias:
    return ModelAlias(
        alias="probe-model",
        upstream_model_name="probe-model",
        litellm_model=litellm_model,
    )


def test_unregisterd_vllm_model_triggers_fake_stream():
    """Precondition: a custom vLLM model name (not in LiteLLM's registry)
    triggers fake-streaming. This is the bug."""
    # Use a name guaranteed not to be in the registry.
    assert _should_fake_stream("openai/definitely-not-a-registered-model-xyz") is True


def test_registration_disables_fake_stream_for_custom_model():
    """After register_model_for_native_streaming, the same custom model must
    use real native streaming (should_fake_stream == False)."""
    litellm_model = "openai/definitely-not-a-registered-model-xyz"
    alias = _make_alias(litellm_model)
    register_model_for_native_streaming(alias)
    assert _should_fake_stream(litellm_model) is False


def test_registration_strips_provider_prefix_for_registry_key():
    """LiteLLM's registry is keyed by the bare model name (without the
    'openai/' prefix). Registration must strip the prefix so the lookup in
    should_fake_stream finds the entry."""
    litellm_model = "openai/prefix-strip-test-model-abc"
    alias = _make_alias(litellm_model)
    register_model_for_native_streaming(alias)
    # Probe with the full litellm_model string (what the gateway actually
    # passes) - should still resolve to the stripped registry entry.
    assert _should_fake_stream(litellm_model) is False


def test_registration_is_idempotent():
    """Repeated registration must not error and must keep native streaming on."""
    litellm_model = "openai/idempotent-test-model-123"
    alias = _make_alias(litellm_model)
    register_model_for_native_streaming(alias)
    register_model_for_native_streaming(alias)
    register_model_for_native_streaming(alias)
    assert _should_fake_stream(litellm_model) is False


def test_registration_handles_model_without_provider_prefix():
    """A litellm_model with no '/' (bare name) must still register correctly."""
    litellm_model = "bare-model-no-prefix-456"
    alias = _make_alias(litellm_model)
    register_model_for_native_streaming(alias)
    assert _should_fake_stream(litellm_model) is False


def test_registration_does_not_break_known_openai_models():
    """Registering custom models must not flip a real OpenAI model (gpt-4o,
    which already supports native streaming) into fake-streaming."""
    # gpt-4o is in the registry and supports native streaming natively.
    assert _should_fake_stream("openai/gpt-4o") is False
    # Registering an unrelated custom model must not change gpt-4o's behavior.
    register_model_for_native_streaming(_make_alias("openai/some-other-model"))
    assert _should_fake_stream("openai/gpt-4o") is False
