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
        # task. Each fact commits in its own session so a single failure (bad
        # enum, type error, constraint violation) is isolated and logged.
        while _pending:
            batch = list(_pending)
            _pending.clear()
            for fact_kwargs, endpoint in batch:
                try:
                    async with AsyncSessionLocal() as session:
                        await record_request_fact(session, **fact_kwargs)
                        await session.commit()
                except Exception:
                    logger.exception(
                        "fact_write_error endpoint=%s", endpoint
                    )
    finally:
        _draining = False
