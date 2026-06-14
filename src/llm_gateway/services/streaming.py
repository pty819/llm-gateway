from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

# SSE comment frame: per the SSE spec, any line starting with ":" is a comment
# and MUST be ignored by clients. OpenAI/Anthropic SDKs, Codex, Claude Code,
# curl, and browsers' EventSource all honor this, so it keeps the gateway->client
# leg alive without corrupting the response stream. Sent when the upstream stays
# silent longer than the keepalive interval (e.g. during long model reasoning).
HEARTBEAT_FRAME = ": keepalive\n\n"


async def iter_with_heartbeat(
    upstream: AsyncIterator[tuple[str, dict[str, Any] | None]],
    *,
    interval_seconds: float,
) -> AsyncIterator[tuple[str, dict[str, Any] | None]]:
    """Forward upstream SSE events, injecting keepalive comment frames while the
    upstream is silent.

    The upstream's exceptions (including ``asyncio.CancelledError`` raised on
    client disconnect) propagate to the consumer unchanged, so the proxy
    handlers' existing ``try``/``except``/``finally`` accounting keeps working.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()
    done = object()

    async def produce() -> None:
        try:
            async for event, usage in upstream:
                await queue.put((event, usage))
        except BaseException as exc:  # propagate to consumer, incl. CancelledError
            await queue.put(exc)
        finally:
            await queue.put(done)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await queue.put((HEARTBEAT_FRAME, None))

    producer = asyncio.create_task(produce())
    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        while True:
            item = await queue.get()
            if item is done:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        for task in (heartbeat_task, producer):
            if not task.done():
                task.cancel()
        await asyncio.gather(heartbeat_task, producer, return_exceptions=True)
