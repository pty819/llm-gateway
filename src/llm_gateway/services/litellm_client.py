import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import httpx2 as httpx
import litellm
from litellm import acompletion, anthropic_messages, aresponses

from llm_gateway.db.models import EndpointFamily, ModelAlias, UpstreamTarget
from llm_gateway.services.facts import extract_usage_dict


class LiteLLMCallResult:
    def __init__(self, response: Any, usage: dict[str, Any] | None):
        self.response = response
        self.usage = usage


ANTHROPIC_DROP_PARAMS = True
OPENAI_CHAT_COMPLETIONS_RESPONSES_PREFIX = "openai/chat_completions/"
# Model prefixes whose /v1/responses traffic is bridged through a
# chat-completions-shaped backend instead of the upstream's native /responses.
BRIDGED_RESPONSES_PREFIXES = (
    OPENAI_CHAT_COMPLETIONS_RESPONSES_PREFIX,
    # LiteLLM has no native Anthropic Responses config, so aresponses routes
    # anthropic/* models through the same generic completion bridge against
    # the upstream's /v1/messages.
    "anthropic/",
)


def configure_litellm_routing() -> None:
    # Claude Code sends Anthropic Messages while vLLM/vLLM-router expose OpenAI
    # chat-completions. Keep this process-wide LiteLLM adapter behavior explicit.
    litellm.use_chat_completions_url_for_anthropic_messages = True


configure_litellm_routing()


def register_model_for_native_streaming(model_alias: ModelAlias) -> None:
    """Register a ModelAlias with LiteLLM so the Responses API uses real
    streaming instead of fake-streaming.

    Root cause being worked around: LiteLLM's ``OpenAIResponsesAPIConfig
    .should_fake_stream`` consults ``supports_native_streaming`` from the
    LiteLLM model registry. Custom vLLM model names (``qwen3``,
    ``Qwen2.5-72B-Instruct``) are NOT in the registry, so
    ``supports_native_streaming`` raises -> returns False -> LiteLLM silently
    drops ``stream=True`` from the upstream request, POSTs a non-streaming
    /responses call to vLLM, then slices the full JSON into fake SSE chunks.

    Symptoms: "stream disconnected before completion" / "error sending for
    url" on the gateway's /v1/responses path, while Codex hitting vLLM
    directly (real stream=True) works fine. vLLM's Responses endpoint DOES
    support native SSE streaming, so we override the registry entry to claim
    the model supports native streaming. This makes LiteLLM send stream=True
    through to vLLM and parse the real SSE events via
    ``ResponsesAPIStreamingIterator``.

    Safe to call repeatedly (register_model upserts). Idempotent across
    workers because the registry is per-process; every worker must call this
    at startup and on model create/update. The registry entry is minimal -
    just enough to flip the fake-stream decision - and does not affect
    pricing or routing for chat-completions (which never consult this flag
    for openai/* models).
    """
    litellm_model = model_alias.litellm_model or ""
    # LiteLLM keys its model_cost registry by the bare model name (without the
    # "openai/" provider prefix). get_model_info / _get_model_info_helper also
    # look up by the stripped name, so we register under the stripped key.
    registry_key = litellm_model.split("/", 1)[1] if "/" in litellm_model else litellm_model
    if not registry_key:
        return
    try:
        litellm.register_model(
            {
                registry_key: {
                    "litellm_provider": "openai",
                    "supports_native_streaming": True,
                    # Mark as a custom/unknown-mode entry so LiteLLM does not
                    # assume chat-completion-only capabilities. ``mode`` is
                    # optional but helps get_model_info succeed without raising.
                    "mode": "chat",
                }
            }
        )
    except Exception:
        # Registration is best-effort: if it ever fails we fall back to the
        # fake-stream behavior (the status quo ante), which still produces
        # correct output for short responses - just not real streaming. Never
        # let a registry hiccough block model creation or startup.
        pass


def _api_key(upstream: UpstreamTarget) -> str | None:
    return upstream.api_key_value or upstream.api_key_ref


def uses_openai_chat_completions_upstream(model_alias: ModelAlias) -> bool:
    return model_alias.litellm_model.lower().startswith(
        OPENAI_CHAT_COMPLETIONS_RESPONSES_PREFIX
    )


def uses_bridged_responses_upstream(model_alias: ModelAlias) -> bool:
    """True when /v1/responses traffic is bridged through a chat-completions-
    shaped backend instead of the upstream's native /responses endpoint.

    Bridged calls must send drop_params=True: clients like Codex always send
    ``reasoning: {"effort": ...}``, which the bridge maps to the completion
    param ``reasoning_effort``. LiteLLM then rejects that param with
    UnsupportedParamsError for model names absent from its registry (any
    custom/enterprise model name) — a deterministic failure for every Codex
    request. Dropping the param is the right call on bridged paths: the
    OpenAI/Anthropic chat backends either ignore reasoning hints or map them
    natively, and silently dropping beats failing the whole stream.
    """
    litellm_model = model_alias.litellm_model.lower()
    return litellm_model.startswith(BRIDGED_RESPONSES_PREFIXES)


def effective_chat_litellm_model(model_alias: ModelAlias) -> str:
    """Normalize a bridge-prefixed litellm_model for the chat-shaped entry
    points (/v1/chat/completions and /v1/messages).

    ``openai/chat_completions/<m>`` is a directive only the Responses path
    understands: litellm's aresponses() rewrites it to ``openai/<m>`` plus the
    bridge flag. Every other litellm entry (acompletion, anthropic_messages)
    splits the string at the first slash and would send the literal
    ``chat_completions/<m>`` as the upstream model name, which no upstream
    serves. Rewrite it here so a bridge-prefixed alias works on all three
    gateway protocols — everything lands on the upstream's /chat/completions.
    """
    litellm_model = model_alias.litellm_model
    if litellm_model.lower().startswith(OPENAI_CHAT_COMPLETIONS_RESPONSES_PREFIX):
        remainder = litellm_model[len(OPENAI_CHAT_COMPLETIONS_RESPONSES_PREFIX) :]
        if remainder:
            return f"openai/{remainder}"
    return litellm_model


def resolve_upstream_call(
    endpoint_family: EndpointFamily, model_alias: ModelAlias
) -> tuple[str, dict[str, Any]]:
    """The single place that turns (entrance, alias) into a litellm payload.

    Returns the model string to send plus any extra litellm kwargs required by
    this entrance×prefix combination. The bridge-prefix directive previously
    had to be reasoned about across four helpers and six call sites; adding a
    new bridge prefix now means updating the predicates above and this one
    resolver, and every entrance picks up the change.

    - Responses entrance: the directive goes through verbatim (litellm's
      aresponses understands it) plus the bridge flags.
    - Chat-shaped entrances: the directive is stripped to ``openai/<m>``;
      no extra params.
    """
    if endpoint_family is EndpointFamily.OPENAI_RESPONSES:
        extra: dict[str, Any] = {}
        if uses_openai_chat_completions_upstream(model_alias):
            extra["use_chat_completions_api"] = True
        if uses_bridged_responses_upstream(model_alias):
            extra["drop_params"] = True
        return model_alias.litellm_model, extra
    return effective_chat_litellm_model(model_alias), {}


def probe_request_parts(upstream: UpstreamTarget) -> tuple[str, dict[str, str]]:
    """URL + headers for a health probe GET against this upstream.

    Single source of truth for probe request construction — the admin manual
    Check (check_upstream_health) and the sidecar's background prober
    (health_checker._probe_upstream) must hit the identical URL shape and
    header injection; they differ only in verdict policy.
    """
    url = upstream.base_url.rstrip("/") + "/" + upstream.health_path.lstrip("/")
    headers = dict(upstream.extra_headers or {})
    api_key = _api_key(upstream)
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
    return url, headers


async def check_upstream_health(
    upstream: UpstreamTarget, timeout_seconds: float = 10.0
) -> dict[str, Any]:
    url, headers = probe_request_parts(upstream)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url, headers=headers)
    return {
        "ok": 200 <= response.status_code < 500,
        "status_code": response.status_code,
        "url": url,
    }


def to_plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return value
    return value


async def completion_once(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> LiteLLMCallResult:
    payload = dict(body)
    payload["model"], bridge_extra = resolve_upstream_call(
        EndpointFamily.OPENAI_CHAT, model_alias
    )
    payload.update(bridge_extra)
    response = await acompletion(
        api_base=upstream.base_url,
        api_key=_api_key(upstream),
        **payload,
    )
    return LiteLLMCallResult(response=response, usage=_usage_from_response(response))


async def upstream_request_once(
    *,
    endpoint_family: EndpointFamily,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> LiteLLMCallResult:
    if endpoint_family == EndpointFamily.OPENAI_CHAT:
        return await completion_once(
            model_alias=model_alias, upstream=upstream, body=body
        )
    if endpoint_family == EndpointFamily.OPENAI_RESPONSES:
        return await responses_once(
            model_alias=model_alias, upstream=upstream, body=body
        )
    if endpoint_family == EndpointFamily.ANTHROPIC_MESSAGES:
        return await anthropic_messages_once(
            model_alias=model_alias, upstream=upstream, body=body
        )
    raise ValueError(f"unsupported endpoint family: {endpoint_family}")


async def completion_stream(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> AsyncGenerator[tuple[str, dict[str, Any] | None], None]:
    payload = dict(body)
    payload["model"], bridge_extra = resolve_upstream_call(
        EndpointFamily.OPENAI_CHAT, model_alias
    )
    payload.update(bridge_extra)
    payload["stream"] = True
    if "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}
    stream = await acompletion(
        api_base=upstream.base_url,
        api_key=_api_key(upstream),
        **payload,
    )
    async for chunk in stream:
        usage = _usage_from_response(chunk)
        yield f"data: {_json_dumps(to_plain(chunk))}\n\n", usage
        await asyncio.sleep(0)
    yield "data: [DONE]\n\n", None


async def upstream_request_stream(
    *,
    endpoint_family: EndpointFamily,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> AsyncGenerator[tuple[str, dict[str, Any] | None], None]:
    if endpoint_family == EndpointFamily.OPENAI_CHAT:
        async for item in completion_stream(
            model_alias=model_alias, upstream=upstream, body=body
        ):
            yield item
        return
    if endpoint_family == EndpointFamily.OPENAI_RESPONSES:
        async for item in responses_stream(
            model_alias=model_alias, upstream=upstream, body=body
        ):
            yield item
        return
    if endpoint_family == EndpointFamily.ANTHROPIC_MESSAGES:
        async for item in anthropic_messages_stream(
            model_alias=model_alias, upstream=upstream, body=body
        ):
            yield item
        return
    raise ValueError(f"unsupported endpoint family: {endpoint_family}")


async def anthropic_messages_once(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> LiteLLMCallResult:
    payload = dict(body)
    payload["model"], bridge_extra = resolve_upstream_call(
        EndpointFamily.ANTHROPIC_MESSAGES, model_alias
    )
    payload.update(bridge_extra)
    payload["drop_params"] = ANTHROPIC_DROP_PARAMS
    response = await anthropic_messages(
        api_base=upstream.base_url,
        api_key=_api_key(upstream),
        **payload,
    )
    return LiteLLMCallResult(response=response, usage=_usage_from_response(response))


async def anthropic_messages_stream(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> AsyncGenerator[tuple[str, dict[str, Any] | None], None]:
    payload = dict(body)
    payload["model"], bridge_extra = resolve_upstream_call(
        EndpointFamily.ANTHROPIC_MESSAGES, model_alias
    )
    payload.update(bridge_extra)
    payload["stream"] = True
    payload["drop_params"] = ANTHROPIC_DROP_PARAMS
    stream = await anthropic_messages(
        api_base=upstream.base_url,
        api_key=_api_key(upstream),
        **payload,
    )
    async for chunk in stream:
        usage = _usage_from_response(chunk)
        yield (
            f"event: content_block_delta\ndata: {_json_dumps(to_plain(chunk))}\n\n",
            usage,
        )
        await asyncio.sleep(0)


def _usage_from_response(response: Any) -> dict[str, Any] | None:
    if isinstance(response, dict):
        return extract_usage_dict(response.get("usage"))
    return extract_usage_dict(getattr(response, "usage", None))


async def responses_once(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> LiteLLMCallResult:
    payload = dict(body)
    payload["model"], bridge_extra = resolve_upstream_call(
        EndpointFamily.OPENAI_RESPONSES, model_alias
    )
    payload.update(bridge_extra)
    response = await aresponses(
        api_base=upstream.base_url,
        api_key=_api_key(upstream),
        **payload,
    )
    usage = _usage_from_responses_api(response)
    return LiteLLMCallResult(response=response, usage=usage)


async def responses_stream(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> AsyncGenerator[tuple[str, dict[str, Any] | None], None]:
    payload = dict(body)
    payload["model"], bridge_extra = resolve_upstream_call(
        EndpointFamily.OPENAI_RESPONSES, model_alias
    )
    payload.update(bridge_extra)
    payload["stream"] = True
    stream = await aresponses(
        api_base=upstream.base_url,
        api_key=_api_key(upstream),
        **payload,
    )
    async for chunk in stream:
        usage = _usage_from_responses_api(chunk)
        yield f"data: {_json_dumps(to_plain(chunk))}\n\n", usage
        await asyncio.sleep(0)
    yield "data: [DONE]\n\n", None


def _usage_from_responses_api(response: Any) -> dict[str, Any] | None:
    if isinstance(response, dict):
        return extract_usage_dict(response.get("usage"))
    usage = getattr(response, "usage", None)
    if usage is not None:
        return extract_usage_dict(usage)
    return None


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(_json_safe(value), ensure_ascii=False, separators=(",", ":"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value
