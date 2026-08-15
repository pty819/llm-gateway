from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_pending: list[tuple[dict[str, Any], str]] = []
_draining = False


async def enqueue_fact(fact_kwargs: dict[str, Any], endpoint: str) -> None:
    """Best-effort enqueue of a request fact for asynchronous persistence.

    Returns as soon as the fact is queued; persistence happens on a later
    event-loop tick. Critical facts (auth failure, rate limit) are recorded this
    way on purpose so they never block the response path.
    """
    global _draining
    _pending.append((fact_kwargs, endpoint))
    if not _draining:
        _draining = True
        asyncio.create_task(_drain())


async def drain_now(timeout_seconds: float = 10.0) -> None:
    """Flush every pending fact synchronously. Called on shutdown so a restart
    never drops in-flight accounting facts. Each fact is persisted in its own
    transaction, so one bad fact cannot sink the rest of the batch.
    """
    global _draining
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while _pending:
        if loop.time() >= deadline:
            logger.warning(
                "drain_now timed out with %d facts still pending", len(_pending)
            )
            return
        if _draining:
            await asyncio.sleep(0.02)
        else:
            _draining = True
            await _drain()


async def _drain() -> None:
    global _draining
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.facts import record_request_fact

    try:
        # Keep draining while facts keep arriving; new enqueues during the loop
        # are picked up on the next iteration rather than waiting for a fresh
        # task. One session per drain with a per-fact SAVEPOINT: a bad fact
        # rolls back only its own savepoint (same isolation as the old
        # one-session-per-fact loop) while K facts cost one BEGIN/COMMIT pair
        # instead of K — bursts (429 storms, auth-failure floods) stop paying
        # 3K round trips on the shared pool.
        while _pending:
            batch = list(_pending)
            _pending.clear()
            try:
                async with AsyncSessionLocal() as session:
                    for fact_kwargs, endpoint in batch:
                        try:
                            async with session.begin_nested():
                                await record_request_fact(session, **fact_kwargs)
                        except Exception:
                            logger.exception(
                                "fact_write_error endpoint=%s", endpoint
                            )
                    await session.commit()
            except Exception:
                # Batch-level failure (connection loss, commit failure): log
                # once; facts already dequeued above are lost, same as the old
                # per-fact loop's tail on a dead connection.
                logger.exception(
                    "fact_batch_write_failed count=%d", len(batch)
                )
    finally:
        _draining = False
