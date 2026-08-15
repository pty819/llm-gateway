"""Redis-backed runtime liveness state for upstream targets.

This is the *runtime* complement to the *configured* state held in
`upstream_targets.state` (PG). Configuration state is durable and admin-owned:
"should this upstream participate in routing at all?" Runtime liveness state is
ephemeral and sidecar-owned: "right now, can we reach it?" The two never overlap
in responsibility — admin edits PG, the health-check sidecar edits Redis.

Key layout: `llm_gateway:upstream:unhealthy:{upstream_id}` → JSON payload
`{reason, since, status_code}`. Presence of the key means "unhealthy"; absence
means "healthy" (the default). Every key carries a TTL so a dead sidecar can
never wedge an upstream permanently — the marker expires and the upstream is
treated as healthy again on its own.

All functions degrade gracefully when Redis is unavailable: the routing path
falls back to trusting only PG configuration state rather than failing closed,
because taking down the data plane over a Redis outage is worse than routing to
an upstream that might be unhealthy.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "llm_gateway:upstream:unhealthy:"


def _key(upstream_id: UUID | str) -> str:
    return f"{_KEY_PREFIX}{upstream_id}"


def _payload(reason: str, status_code: int | None) -> str:
    return json.dumps(
        {
            "reason": reason,
            "status_code": status_code,
            "since": datetime.now(timezone.utc).isoformat(),
        }
    )


async def mark_unhealthy(
    redis: Redis,
    upstream_id: UUID | str,
    *,
    reason: str,
    status_code: int | None,
    ttl_seconds: int,
) -> None:
    """Set the UNHEALTHY marker with a TTL. Idempotent — repeated calls refresh.

    The TTL is the auto-recovery guarantee: even if the sidecar crashes after
    marking, the upstream recovers on its own once the TTL expires. A subsequent
    failed probe refreshes the TTL; a passing probe deletes the key.
    """
    await redis.set(_key(upstream_id), _payload(reason, status_code), ex=ttl_seconds)


async def clear_unhealthy(redis: Redis, upstream_id: UUID | str) -> None:
    """Delete the UNHEALTHY marker, marking the upstream healthy again.

    Called on a passing probe. Missing-key is a no-op (already healthy).
    """
    await redis.delete(_key(upstream_id))


async def filter_unhealthy(
    redis: Redis | None,
    upstream_ids: list[UUID | str],
) -> set[str]:
    """Return the set of upstream_ids (as str) that carry an UNHEALTHY marker.

    Batched via MGET so N upstreams cost one Redis round-trip. Callers subtract
    this set from their candidate list to apply runtime liveness filtering.

    Redis None/unreachable → empty set (degrade-open: trust PG config alone).
    A Redis outage must not take down the data plane; routing falls back to
    configured state, accepting that a stale-unhealthy upstream may receive
    traffic until Redis recovers.
    """
    if not upstream_ids:
        return set()
    if redis is None:
        return set()
    try:
        keys = [_key(uid) for uid in upstream_ids]
        values = await redis.mget(keys)
    except Exception:
        logger.exception("upstream_health_filter_redis_failed")
        return set()
    unhealthy: set[str] = set()
    for uid, value in zip(upstream_ids, values, strict=True):
        if value is not None:
            unhealthy.add(str(uid))
    return unhealthy


def parse_payload(raw: str | bytes | None) -> dict[str, Any] | None:
    """Decode an UNHEALTHY marker payload, tolerating legacy/corrupt values."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {"raw": raw}
    except (json.JSONDecodeError, TypeError):
        return {"raw": raw}
