from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_pending: list[tuple[dict[str, Any], str]] = []
_draining = False


async def enqueue_fact(fact_kwargs: dict[str, Any], endpoint: str) -> None:
    global _draining
    _pending.append((fact_kwargs, endpoint))
    if not _draining:
        _draining = True
        asyncio.create_task(_drain())


async def drain_now() -> None:
    if _pending:
        await _drain()


async def _drain() -> None:
    global _draining
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.facts import record_request_fact

    batch = list(_pending)
    _pending.clear()
    if not batch:
        _draining = False
        return

    try:
        async with AsyncSessionLocal() as session:
            for fact_kwargs, endpoint in batch:
                try:
                    await record_request_fact(session, **fact_kwargs)
                except Exception:
                    logger.exception("fact_write_error endpoint=%s", endpoint)
            await session.commit()
    except Exception:
        logger.exception("fact_batch_commit_error batch_size=%d", len(batch))
    finally:
        _draining = False
