from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
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
    disconnect_check: Callable[[], Awaitable[bool]] | None = None,
    disconnect_interval: float = 5.0,
) -> AsyncIterator[tuple[str, dict[str, Any] | None]]:
    """Forward upstream SSE events, injecting keepalive comment frames while the
    upstream is silent.

    The upstream's exceptions (including ``asyncio.CancelledError`` raised on
    client disconnect) propagate to the consumer unchanged, so the proxy
    handlers' existing ``try``/``except``/``finally`` accounting keeps working.

    When ``disconnect_check`` is provided and ``disconnect_interval > 0``, a
    watchdog periodically awaits it; on detecting a disconnect it cancels the
    producer task, which injects a ``CancelledError`` into ``produce()`` and
    rides the existing exception propagation chain to the consumer (no sentinel
    needed). When ``disconnect_check`` is ``None`` or ``disconnect_interval``
    is non-positive, behavior is identical to the no-watchdog path.
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

    async def watchdog() -> None:
        while True:
            await asyncio.sleep(disconnect_interval)
            # Treat a failing disconnect_check as "not disconnected" rather than
            # letting the watchdog die: a dead watchdog silently disables
            # disconnect detection for the rest of the stream. Continue polling
            # so a transient error in the Request object doesn't strand the slot.
            try:
                disconnected = await disconnect_check()
            except Exception:
                continue
            if disconnected:
                producer.cancel()
                return

    producer = asyncio.create_task(produce())
    heartbeat_task = asyncio.create_task(heartbeat())
    watchdog_task: asyncio.Task[None] | None = None
    if disconnect_check is not None and disconnect_interval > 0:
        watchdog_task = asyncio.create_task(watchdog())
    try:
        while True:
            item = await queue.get()
            if item is done:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        for task in (heartbeat_task, watchdog_task, producer):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(heartbeat_task, producer, return_exceptions=True)
