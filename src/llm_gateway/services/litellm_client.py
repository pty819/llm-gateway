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


def configure_litellm_routing() -> None:
    # Claude Code sends Anthropic Messages while vLLM/vLLM-router expose OpenAI
    # chat-completions. Keep this process-wide LiteLLM adapter behavior explicit.
    litellm.use_chat_completions_url_for_anthropic_messages = True


configure_litellm_routing()


def _api_key(upstream: UpstreamTarget) -> str | None:
    return upstream.api_key_value or upstream.api_key_ref


def uses_openai_chat_completions_upstream(model_alias: ModelAlias) -> bool:
    return model_alias.litellm_model.lower().startswith(
        OPENAI_CHAT_COMPLETIONS_RESPONSES_PREFIX
    )


async def check_upstream_health(
    upstream: UpstreamTarget, timeout_seconds: float = 10.0
) -> dict[str, Any]:
    url = upstream.base_url.rstrip("/") + "/" + upstream.health_path.lstrip("/")
    headers = dict(upstream.extra_headers or {})
    api_key = _api_key(upstream)
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url, headers=headers)
    return {
        "ok": 200 <= response.status_code < 500,
        "status_code": response.status_code,
        "url": url,
    }


def _to_plain(value: Any) -> Any:
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
    payload["model"] = model_alias.litellm_model
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
    payload["model"] = model_alias.litellm_model
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
        yield f"data: {_json_dumps(_to_plain(chunk))}\n\n", usage
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
    payload["model"] = model_alias.litellm_model
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
    payload["model"] = model_alias.litellm_model
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
            f"event: content_block_delta\ndata: {_json_dumps(_to_plain(chunk))}\n\n",
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
    payload["model"] = model_alias.litellm_model
    if uses_openai_chat_completions_upstream(model_alias):
        payload["use_chat_completions_api"] = True
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
    payload["model"] = model_alias.litellm_model
    payload["stream"] = True
    if uses_openai_chat_completions_upstream(model_alias):
        payload["use_chat_completions_api"] = True
    stream = await aresponses(
        api_base=upstream.base_url,
        api_key=_api_key(upstream),
        **payload,
    )
    async for chunk in stream:
        usage = _usage_from_responses_api(chunk)
        yield f"data: {_json_dumps(_to_plain(chunk))}\n\n", usage
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
