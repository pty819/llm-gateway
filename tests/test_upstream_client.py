from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx2 as httpx
import pytest

from llm_gateway.db.models import EndpointFamily, ModelAlias, UpstreamTarget
from llm_gateway.services import upstream_client

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _model_alias(litellm_model: str = "test-model") -> ModelAlias:
    return ModelAlias(
        alias="test-model",
        upstream_model_name="test-model",
        litellm_model=litellm_model,
    )


def _upstream() -> UpstreamTarget:
    return UpstreamTarget(
        model_alias_id=uuid4(),
        name="test-upstream",
        base_url="http://upstream.local/v1",
        api_key_value="sk-test",
        extra_headers={"X-Custom": "yes"},
    )


def _patch_client(monkeypatch, handler):
    """Patch upstream_client.httpx.AsyncClient to use a MockTransport wrapping
    ``handler``. The factory merges any kwargs the caller passes (e.g.
    ``timeout``) and injects the mock transport only when not already supplied.
    """
    original = upstream_client.httpx.AsyncClient

    def factory(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return original(**kwargs)

    monkeypatch.setattr(upstream_client.httpx, "AsyncClient", factory)


def _json_response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload),
        headers={"content-type": "application/json"},
    )


def _sse_response(lines: list[str]) -> httpx.Response:
    """Build an SSE response body. Each string in ``lines`` is emitted as one
    line followed by a blank-line frame terminator (``\\n\\n``)."""
    body = "".join(line + "\n\n" for line in lines)
    return httpx.Response(
        status_code=200,
        content=body.encode(),
        headers={"content-type": "text/event-stream"},
    )


def _sse_response_frames(frames: list[list[str]]) -> httpx.Response:
    """Build an SSE response body from explicit multi-line frames.

    Each frame is a list of lines (e.g. ``["event: x", "data: {...}"]``); the
    lines are joined with ``\\n`` and the frame is terminated with ``\\n\\n``.
    """
    body = ""
    for frame_lines in frames:
        body += "\n".join(frame_lines) + "\n\n"
    return httpx.Response(
        status_code=200,
        content=body.encode(),
        headers={"content-type": "text/event-stream"},
    )


async def test_chat_once_posts_to_chat_completions_and_extracts_usage(monkeypatch):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return _json_response(
            {
                "id": "chatcmpl-1",
                "choices": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }
        )

    _patch_client(monkeypatch, handler)

    result = await upstream_client.upstream_request_once(
        endpoint_family=EndpointFamily.OPENAI_CHAT,
        model_alias=_model_alias(litellm_model="gpt-4o"),
        upstream=_upstream(),
        body={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
    )

    assert captured["url"] == "http://upstream.local/v1/chat/completions"
    assert captured["body"]["model"] == "gpt-4o"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert captured["headers"]["x-custom"] == "yes"
    assert result.usage == {"prompt_tokens": 5, "completion_tokens": 2}
    assert result.response == {
        "id": "chatcmpl-1",
        "choices": [],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }


async def test_responses_once_posts_to_responses_endpoint(monkeypatch):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return _json_response(
            {
                "id": "resp-1",
                "output": [],
                "usage": {"input_tokens": 9, "output_tokens": 1},
            }
        )

    _patch_client(monkeypatch, handler)

    result = await upstream_client.upstream_request_once(
        endpoint_family=EndpointFamily.OPENAI_RESPONSES,
        model_alias=_model_alias(),
        upstream=_upstream(),
        body={"input": "hi", "max_output_tokens": 8},
    )

    assert captured["url"] == "http://upstream.local/v1/responses"
    assert captured["body"]["model"] == "test-model"
    assert result.usage == {"input_tokens": 9, "output_tokens": 1}


async def test_chat_once_raises_on_error_status(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"error": "bad"}, status_code=500)

    _patch_client(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        await upstream_client.upstream_request_once(
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            model_alias=_model_alias(),
            upstream=_upstream(),
            body={"messages": []},
        )


async def test_upstream_request_once_rejects_anthropic_family():
    with pytest.raises(ValueError):
        await upstream_client.upstream_request_once(
            endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
            model_alias=_model_alias(),
            upstream=_upstream(),
            body={},
        )


async def test_chat_stream_forwards_sse_lines_and_extracts_usage(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4}}',
        "data: [DONE]",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(lines)

    _patch_client(monkeypatch, handler)

    chunks = [
        item
        async for item in upstream_client.upstream_request_stream(
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            model_alias=_model_alias(),
            upstream=_upstream(),
            body={"messages": [{"role": "user", "content": "hi"}]},
        )
    ]

    # 3 lines forwarded, last usage extracted from the second data chunk.
    assert len(chunks) == 3
    assert chunks[0][0].startswith('data: {"choices"')
    assert chunks[1][1] == {"prompt_tokens": 3, "completion_tokens": 4}
    assert chunks[2][0] == "data: [DONE]\n\n"
    assert chunks[2][1] is None


async def test_chat_stream_injects_include_usage_stream_options(monkeypatch):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _sse_response(["data: [DONE]"])

    _patch_client(monkeypatch, handler)

    _ = [
        item
        async for item in upstream_client.upstream_request_stream(
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            model_alias=_model_alias(),
            upstream=_upstream(),
            body={"messages": [{"role": "user", "content": "hi"}]},
        )
    ]

    assert captured["body"]["stream"] is True
    assert captured["body"]["stream_options"] == {"include_usage": True}


async def test_responses_stream_preserves_multiline_event_frames(monkeypatch):
    """Responses API SSE frames have an ``event:`` line AND a ``data:`` line.
    The gateway must forward them as a single frame (lines joined by ``\\n``,
    terminated by ``\\n\\n``), not split them into separate frames."""
    frames = [
        [
            "event: response.created",
            'data: {"type":"response.created","id":"resp_1"}',
        ],
        [
            "event: response.output_text.delta",
            'data: {"type":"response.output_text.delta","delta":"Hello"}',
        ],
        [
            "event: response.completed",
            'data: {"type":"response.completed","usage":{"input_tokens":7,"output_tokens":2}}',
        ],
        ["data: [DONE]"],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response_frames(frames)

    _patch_client(monkeypatch, handler)

    chunks = [
        item
        async for item in upstream_client.upstream_request_stream(
            endpoint_family=EndpointFamily.OPENAI_RESPONSES,
            model_alias=_model_alias(),
            upstream=_upstream(),
            body={"input": "hi"},
        )
    ]

    # 4 frames forwarded verbatim, each as one unit.
    assert len(chunks) == 4
    # First frame: event + data lines joined by \n, terminated by \n\n
    assert chunks[0][0] == (
        'event: response.created\ndata: {"type":"response.created","id":"resp_1"}\n\n'
    )
    # Usage extracted from the response.completed frame's data line.
    assert chunks[2][1] == {"input_tokens": 7, "output_tokens": 2}
    # [DONE] frame forwarded as-is, no extra [DONE] appended (saw_done=True).
    assert chunks[3][0] == "data: [DONE]\n\n"


async def test_check_upstream_health_gets_health_path(monkeypatch):
    upstream = UpstreamTarget(
        model_alias_id=uuid4(),
        name="test-upstream",
        base_url="http://upstream.local/v1",
        health_path="/models",
        api_key_value="sk-test",
    )

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(status_code=200, content=b'{"data":[]}')

    _patch_client(monkeypatch, handler)

    result = await upstream_client.check_upstream_health(upstream, timeout_seconds=5.0)

    assert captured["url"] == "http://upstream.local/v1/models"
    assert result["ok"] is True
    assert result["status_code"] == 200


async def test_check_upstream_health_marks_5xx_unhealthy(monkeypatch):
    upstream = UpstreamTarget(
        model_alias_id=uuid4(),
        name="test-upstream",
        base_url="http://upstream.local/v1",
        health_path="/models",
        api_key_value="sk-test",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=503, content=b'{"error":"down"}')

    _patch_client(monkeypatch, handler)

    result = await upstream_client.check_upstream_health(upstream, timeout_seconds=5.0)

    assert result["ok"] is False
    assert result["status_code"] == 503


async def test_unified_dispatch_routes_chat_and_responses(monkeypatch):
    seen: list[EndpointFamily] = []
    expected_paths = {
        EndpointFamily.OPENAI_CHAT: "/chat/completions",
        EndpointFamily.OPENAI_RESPONSES: "/responses",
    }
    actual_paths: list[str] = []

    async def fake_post(*, path, model_alias, upstream, body):
        # Map the path back to the family that produced it.
        for fam, fam_path in expected_paths.items():
            if fam_path == path:
                seen.append(fam)
                break
        actual_paths.append(path)
        return upstream_client.UpstreamCallResult(response={"kind": path}, usage=None)

    monkeypatch.setattr(upstream_client, "_post_once", fake_post)

    for endpoint_family in (
        EndpointFamily.OPENAI_CHAT,
        EndpointFamily.OPENAI_RESPONSES,
    ):
        await upstream_client.upstream_request_once(
            endpoint_family=endpoint_family,
            model_alias=_model_alias(),
            upstream=_upstream(),
            body={},
        )

    assert seen == [EndpointFamily.OPENAI_CHAT, EndpointFamily.OPENAI_RESPONSES]
    assert actual_paths == ["/chat/completions", "/responses"]


async def test_litellm_call_result_alias_is_upstream_call_result():
    assert upstream_client.LiteLLMCallResult is upstream_client.UpstreamCallResult
