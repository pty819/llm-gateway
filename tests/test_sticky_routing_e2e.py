"""End-to-end tests for sticky routing through the full proxy stack.

These tests start real asyncio HTTP servers (stdlib only, no new deps) that
mimic a minimal OpenAI Chat Completions endpoint, point two ``UpstreamTarget``
rows at them, and send real ``POST /v1/chat/completions`` requests through the
gateway ASGI app. The mock response embeds the server's unique ID in the
``content`` field so the test can assert which upstream was hit — proving that
requests from the same gateway key stick to the same upstream.

The proxy flow exercised here is:
    POST /v1/chat/completions
      -> _proxy_endpoint
      -> resolve_route_context (select_upstream_for_key + Redis sticky write)
      -> upstream_request_once -> _post_once -> httpx.AsyncClient.post(<mock>)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from llm_gateway.db.models import ModelAlias, UpstreamTarget
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.rate_limit import redis_client
from llm_gateway.services.security import create_gateway_key
from llm_gateway.services.upstream_routing import sticky_route_key
from tests.helpers import _auth_headers

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Mock HTTP server
# ---------------------------------------------------------------------------


def _mock_response_body(server_id: str) -> bytes:
    body = {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": server_id},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return json.dumps(body).encode("utf-8")


async def _handle_mock_connection(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, server_id: str
) -> None:
    """Minimal HTTP/1.1 request reader: parse request line + headers, consume
    the body (Content-Length), then write a fixed JSON response.

    Only enough to satisfy httpx — not a full HTTP parser.
    """
    try:
        # Read request line
        request_line = await reader.readline()
        if not request_line:
            return
        # Read headers until blank line
        content_length = 0
        while True:
            header_line = await reader.readline()
            if header_line in (b"\r\n", b"\n", b""):
                break
            lower = header_line.lower()
            if lower.startswith(b"content-length:"):
                try:
                    content_length = int(lower.split(b":", 1)[1].strip())
                except ValueError:
                    content_length = 0
        # Consume the full request body so httpx doesn't see a connection reset
        if content_length > 0:
            await reader.readexactly(content_length)

        body = _mock_response_body(server_id)
        head = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        writer.write(head + body)
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


@dataclass
class MockServer:
    server_id: str
    server: asyncio.base_events.Server
    port: int
    base_url: str
    _closed: bool = field(default=False)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.server.close()
        try:
            await asyncio.wait_for(self.server.wait_closed(), timeout=5.0)
        except Exception:
            # Best-effort teardown; never fail a test on server close.
            pass


async def start_mock_server(server_id: str) -> MockServer:
    """Start a mock HTTP server on 127.0.0.1 with an OS-assigned port."""
    server = await asyncio.start_server(
        lambda r, w: _handle_mock_connection(r, w, server_id=server_id),
        "127.0.0.1",
        0,
    )
    port = server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    return MockServer(server_id=server_id, server=server, port=port, base_url=base_url)


# ---------------------------------------------------------------------------
# Shared test setup helpers
# ---------------------------------------------------------------------------


@dataclass
class UpstreamPair:
    server_a: MockServer
    server_b: MockServer
    upstream_a_id: object
    upstream_b_id: object


async def _configure_two_mock_upstreams(
    gateway_fixture,
    *,
    mock_a: MockServer,
    mock_b: MockServer,
    sticky_ttl_seconds: int = 300,
) -> UpstreamPair:
    """Point the gateway_fixture's model_alias at two mock upstreams.

    The original upstream created by ``gateway_fixture`` (pointing at the real
    upstream) is DISABLED so only the two mock upstreams are routing candidates.
    The model_alias's ``sticky_ttl_seconds`` is set so sticky expiry is
    deterministic per-test.
    """
    async with AsyncSessionLocal() as session:
        # Set sticky TTL on the alias.
        alias = await session.get(ModelAlias, gateway_fixture.model_alias_id)
        assert alias is not None
        alias.sticky_ttl_seconds = sticky_ttl_seconds

        # Disable the original (real) upstream.
        original = await session.get(UpstreamTarget, gateway_fixture.upstream_id)
        assert original is not None
        from llm_gateway.db.models import ResourceState

        original.state = ResourceState.DISABLED

        # Create two sibling upstreams pointing at the mock servers.
        upstream_a = UpstreamTarget(
            model_alias_id=gateway_fixture.model_alias_id,
            name=f"pytest-mock-upstream-a-{uuid4().hex[:8]}",
            base_url=mock_a.base_url,
        )
        upstream_b = UpstreamTarget(
            model_alias_id=gateway_fixture.model_alias_id,
            name=f"pytest-mock-upstream-b-{uuid4().hex[:8]}",
            base_url=mock_b.base_url,
        )
        session.add(upstream_a)
        session.add(upstream_b)
        await session.flush()
        await session.commit()
        return UpstreamPair(
            server_a=mock_a,
            server_b=mock_b,
            upstream_a_id=upstream_a.id,
            upstream_b_id=upstream_b.id,
        )


async def _clear_sticky_key(gateway_fixture, *, key_id=None) -> None:
    """Delete the sticky Redis key for a key × model_alias."""
    kid = key_id if key_id is not None else gateway_fixture.key_id
    key = sticky_route_key(key_id=kid, model_alias_id=gateway_fixture.model_alias_id)
    await redis_client.delete(key)


async def _send_chat(client, gateway_fixture, *, raw_key=None) -> str:
    """Send a non-streaming chat completion and return the upstream server_id
    embedded in the response content."""
    request_id = f"pytest-sticky-{uuid4()}"
    key = raw_key if raw_key is not None else gateway_fixture.raw_key
    response = await client.post(
        "/v1/chat/completions",
        headers=_auth_headers(key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
            "stream": False,
        },
    )
    assert response.status_code == 200, response.text
    content = response.json()["choices"][0]["message"]["content"]
    return content


async def _sticky_ttl(gateway_fixture, *, key_id=None) -> int:
    """Return the remaining TTL (seconds) of the sticky key in Redis."""
    kid = key_id if key_id is not None else gateway_fixture.key_id
    key = sticky_route_key(key_id=kid, model_alias_id=gateway_fixture.model_alias_id)
    return await redis_client.ttl(key)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_same_key_sticks_to_same_upstream(client, gateway_fixture):
    """Requests from the same gateway key must all hit the same mock server."""
    mock_a = await start_mock_server("server-A")
    mock_b = await start_mock_server("server-B")
    try:
        await _configure_two_mock_upstreams(
            gateway_fixture, mock_a=mock_a, mock_b=mock_b, sticky_ttl_seconds=300
        )
        await _clear_sticky_key(gateway_fixture)

        hit_servers = [await _send_chat(client, gateway_fixture) for _ in range(5)]

        assert len(set(hit_servers)) == 1, (
            f"Expected all 5 requests to stick to one server, got {hit_servers}"
        )
        assert hit_servers[0] in {"server-A", "server-B"}
    finally:
        await mock_a.aclose()
        await mock_b.aclose()


async def test_different_keys_have_independent_sticky(client, gateway_fixture):
    """Two different gateway keys maintain independent sticky upstreams."""
    mock_a = await start_mock_server("server-A")
    mock_b = await start_mock_server("server-B")
    try:
        await _configure_two_mock_upstreams(
            gateway_fixture, mock_a=mock_a, mock_b=mock_b, sticky_ttl_seconds=300
        )

        # Create a second gateway key for the same subject/project.
        async with AsyncSessionLocal() as session:
            key2, raw_key2 = await create_gateway_key(
                session,
                subject_id=gateway_fixture.subject_id,
                project_id=gateway_fixture.project_id,
                name=f"pytest-key2-{uuid4().hex[:8]}",
            )
            await session.commit()
            key2_id = key2.id

        await _clear_sticky_key(gateway_fixture)
        await _clear_sticky_key(gateway_fixture, key_id=key2_id)

        # 3 requests with key1, 3 with key2 (interleaved to stress independence).
        hits_key1: list[str] = []
        hits_key2: list[str] = []
        for _ in range(3):
            hits_key1.append(await _send_chat(client, gateway_fixture))
            hits_key2.append(await _send_chat(client, gateway_fixture, raw_key=raw_key2))

        # Each key's requests must all hit the same server (sticky held).
        assert len(set(hits_key1)) == 1, (
            f"key1 requests should stick to one server, got {hits_key1}"
        )
        assert len(set(hits_key2)) == 1, (
            f"key2 requests should stick to one server, got {hits_key2}"
        )
        # key1 and key2 MAY resolve to the same or different servers — the
        # assertion is independence of the sticky state, not divergence.
        assert hits_key1[0] in {"server-A", "server-B"}
        assert hits_key2[0] in {"server-A", "server-B"}
    finally:
        await mock_a.aclose()
        await mock_b.aclose()


async def test_sticky_expires_after_ttl(client, gateway_fixture):
    """After the sticky TTL elapses the Redis key is gone, and a fresh request
    writes a new sticky entry."""
    mock_a = await start_mock_server("server-A")
    mock_b = await start_mock_server("server-B")
    try:
        await _configure_two_mock_upstreams(
            gateway_fixture, mock_a=mock_a, mock_b=mock_b, sticky_ttl_seconds=1
        )
        await _clear_sticky_key(gateway_fixture)

        # First request writes the sticky key (TTL = 1s).
        first_server = await _send_chat(client, gateway_fixture)
        ttl_after_first = await _sticky_ttl(gateway_fixture)
        assert ttl_after_first > 0, "sticky key should exist right after first request"

        # Wait for the TTL to expire.
        await asyncio.sleep(2.0)
        ttl_after_sleep = await _sticky_ttl(gateway_fixture)
        assert ttl_after_sleep == -2, (
            f"sticky key should be expired (TTL -2) after 2s sleep, got {ttl_after_sleep}"
        )

        # A new request should write a fresh sticky key.
        second_server = await _send_chat(client, gateway_fixture)
        ttl_after_second = await _sticky_ttl(gateway_fixture)
        assert ttl_after_second > 0, "sticky key should be re-written after a post-expiry request"
        assert second_server in {"server-A", "server-B"}
        assert first_server in {"server-A", "server-B"}
    finally:
        await mock_a.aclose()
        await mock_b.aclose()


async def test_sticky_refreshes_on_each_request(client, gateway_fixture):
    """Each request refreshes the sticky TTL, so the key never expires as long
    as requests keep arriving within the TTL window."""
    mock_a = await start_mock_server("server-A")
    mock_b = await start_mock_server("server-B")
    try:
        await _configure_two_mock_upstreams(
            gateway_fixture, mock_a=mock_a, mock_b=mock_b, sticky_ttl_seconds=2
        )
        await _clear_sticky_key(gateway_fixture)

        hit_servers: list[str] = []

        # Request 1: establishes sticky key with TTL ~2s.
        hit_servers.append(await _send_chat(client, gateway_fixture))
        ttl1 = await _sticky_ttl(gateway_fixture)
        assert 0 < ttl1 <= 2, f"TTL after req1 should be ~2s, got {ttl1}"

        # Sleep 1s (half the TTL), send req2 -> TTL refreshed to ~2s again.
        await asyncio.sleep(1.0)
        hit_servers.append(await _send_chat(client, gateway_fixture))
        ttl2 = await _sticky_ttl(gateway_fixture)
        assert 0 < ttl2 <= 2, f"TTL after req2 should be refreshed to ~2s, got {ttl2}"
        assert ttl2 > 1, f"TTL should be refreshed upward (>1s remaining), got {ttl2}"

        # Sleep 1s, send req3 -> refreshed again.
        await asyncio.sleep(1.0)
        hit_servers.append(await _send_chat(client, gateway_fixture))
        ttl3 = await _sticky_ttl(gateway_fixture)
        assert 0 < ttl3 <= 2, f"TTL after req3 should be refreshed, got {ttl3}"
        assert ttl3 > 1, f"TTL should be refreshed upward, got {ttl3}"

        # Sleep 1s, send req4 -> refreshed again.
        await asyncio.sleep(1.0)
        hit_servers.append(await _send_chat(client, gateway_fixture))
        ttl4 = await _sticky_ttl(gateway_fixture)
        assert 0 < ttl4 <= 2, f"TTL after req4 should be refreshed, got {ttl4}"
        assert ttl4 > 1, f"TTL should be refreshed upward, got {ttl4}"

        # Over 3 seconds elapsed with TTL=2s: without refresh the key would
        # have expired at t=2s. It's still valid -> refresh works.
        # Also all requests stuck to the same server.
        assert len(set(hit_servers)) == 1, (
            f"All 4 requests should hit the same server (sticky held), got {hit_servers}"
        )
    finally:
        await mock_a.aclose()
        await mock_b.aclose()
