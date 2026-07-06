"""Direct upstream HTTP client.

Replaces the former LiteLLM adapter. The gateway now forwards OpenAI Chat
Completions and OpenAI Responses requests verbatim to the upstream using
httpx2 — no protocol translation, no provider routing, no param dropping.
The request body is sent as-is (with ``model`` overwritten to the alias's
``litellm_model`` value, which now holds a bare upstream model name), and the
response body is forwarded as-is. Token usage is read straight off the
upstream response's ``usage`` field.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx2 as httpx

from llm_gateway.core.config import get_settings
from llm_gateway.db.models import EndpointFamily, ModelAlias, UpstreamTarget
from llm_gateway.services.facts import extract_usage_dict


class UpstreamCallResult:
    def __init__(self, response: Any, usage: dict[str, Any] | None):
        self.response = response
        self.usage = usage


# Backwards-compatible alias so call sites and tests that still reference the
# old name keep working. New code should use UpstreamCallResult.
LiteLLMCallResult = UpstreamCallResult


def _api_key(upstream: UpstreamTarget) -> str | None:
    return upstream.api_key_value or upstream.api_key_ref


def _headers(upstream: UpstreamTarget) -> dict[str, str]:
    headers = dict(upstream.extra_headers or {})
    api_key = _api_key(upstream)
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
    headers.setdefault("Accept", "application/json")
    return headers


def _timeout() -> float:
    return get_settings().upstream_timeout_seconds


def _url(upstream: UpstreamTarget, path: str) -> str:
    return upstream.base_url.rstrip("/") + "/" + path.lstrip("/")


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


def _prepare_payload(model_alias: ModelAlias, body: dict[str, Any]) -> dict[str, Any]:
    payload = dict(body)
    payload["model"] = model_alias.litellm_model
    return payload


async def _iter_sse_frames(
    response: httpx.Response,
) -> AsyncGenerator[tuple[str, dict[str, Any] | None], None]:
    """Yield complete SSE frames from an upstream streaming response.

    An SSE frame is one or more non-empty lines terminated by a blank line.
    The frame is yielded verbatim (lines joined with ``\\n`` plus a trailing
    ``\\n\\n``). This correctly handles both Chat Completions (one ``data:``
    line per frame) and Responses API (``event:`` + ``data:`` lines per frame)
    wire formats — the event/data binding within a frame is preserved.

    If a ``data:`` line carries a JSON payload with a ``usage`` field, it is
    extracted and yielded alongside the frame for accounting.
    """
    buffer: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if buffer:
                frame = "\n".join(buffer) + "\n\n"
                usage = _extract_usage_from_frame(buffer)
                yield frame, usage
                buffer = []
            continue
        buffer.append(line)
    # Flush any trailing frame without a blank line terminator.
    if buffer:
        frame = "\n".join(buffer) + "\n\n"
        usage = _extract_usage_from_frame(buffer)
        yield frame, usage


def _extract_usage_from_frame(lines: list[str]) -> dict[str, Any] | None:
    """Parse the ``data:`` line of an SSE frame and extract usage if present."""
    for line in lines:
        if not line.startswith("data: ") or line.startswith("data: [DONE]"):
            continue
        try:
            chunk = json.loads(line[len("data: ") :])
        except (json.JSONDecodeError, AttributeError):
            continue
        usage = extract_usage_dict(chunk.get("usage"))
        if usage is not None:
            return usage
    return None


async def _chat_once(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> UpstreamCallResult:
    payload = _prepare_payload(model_alias, body)
    url = _url(upstream, "/chat/completions")
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        response = await client.post(url, json=payload, headers=_headers(upstream))
    response.raise_for_status()
    data = response.json()
    return UpstreamCallResult(response=data, usage=extract_usage_dict(data.get("usage")))


async def _chat_stream(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> AsyncGenerator[tuple[str, dict[str, Any] | None], None]:
    payload = _prepare_payload(model_alias, body)
    payload["stream"] = True
    if "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}
    url = _url(upstream, "/chat/completions")
    saw_done = False
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        async with client.stream(
            "POST", url, json=payload, headers=_headers(upstream)
        ) as response:
            response.raise_for_status()
            async for frame, usage in _iter_sse_frames(response):
                if "data: [DONE]" in frame:
                    saw_done = True
                yield frame, usage
                await asyncio.sleep(0)
    # Not every upstream emits the [DONE] sentinel (e.g. MiniMax). Emit it
    # ourselves so OpenAI SDK clients that block on it can complete cleanly.
    if not saw_done:
        yield "data: [DONE]\n\n", None


async def _responses_once(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> UpstreamCallResult:
    payload = _prepare_payload(model_alias, body)
    url = _url(upstream, "/responses")
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        response = await client.post(url, json=payload, headers=_headers(upstream))
    response.raise_for_status()
    data = response.json()
    return UpstreamCallResult(response=data, usage=extract_usage_dict(data.get("usage")))


async def _responses_stream(
    *,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> AsyncGenerator[tuple[str, dict[str, Any] | None], None]:
    payload = _prepare_payload(model_alias, body)
    payload["stream"] = True
    url = _url(upstream, "/responses")
    saw_done = False
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        async with client.stream(
            "POST", url, json=payload, headers=_headers(upstream)
        ) as response:
            response.raise_for_status()
            async for frame, usage in _iter_sse_frames(response):
                if "data: [DONE]" in frame:
                    saw_done = True
                yield frame, usage
                await asyncio.sleep(0)
    if not saw_done:
        yield "data: [DONE]\n\n", None


async def upstream_request_once(
    *,
    endpoint_family: EndpointFamily,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> UpstreamCallResult:
    if endpoint_family == EndpointFamily.OPENAI_CHAT:
        return await _chat_once(
            model_alias=model_alias, upstream=upstream, body=body
        )
    if endpoint_family == EndpointFamily.OPENAI_RESPONSES:
        return await _responses_once(
            model_alias=model_alias, upstream=upstream, body=body
        )
    raise ValueError(f"unsupported endpoint family: {endpoint_family}")


async def upstream_request_stream(
    *,
    endpoint_family: EndpointFamily,
    model_alias: ModelAlias,
    upstream: UpstreamTarget,
    body: dict[str, Any],
) -> AsyncGenerator[tuple[str, dict[str, Any] | None], None]:
    if endpoint_family == EndpointFamily.OPENAI_CHAT:
        async for item in _chat_stream(
            model_alias=model_alias, upstream=upstream, body=body
        ):
            yield item
        return
    if endpoint_family == EndpointFamily.OPENAI_RESPONSES:
        async for item in _responses_stream(
            model_alias=model_alias, upstream=upstream, body=body
        ):
            yield item
        return
    raise ValueError(f"unsupported endpoint family: {endpoint_family}")
