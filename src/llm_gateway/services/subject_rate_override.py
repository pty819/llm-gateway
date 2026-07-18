"""Redis-backed per-subject rate-limit overrides (concurrency + RPM).

This is a *higher-priority* complement to the PG-backed `RatePolicy` table.
A per-subject override, when set for a given dimension (RPM or concurrency),
takes absolute precedence over every other source (env defaults, key/project/
subject RatePolicies). The override is intentionally NOT a min() participant —
it short-circuits resolution entirely for the dimensions it covers.

Storage layout::

    llm_gateway:rate:subject_override:{subject_id}
      -> JSON {"rpm": int|null, "concurrency": int|null, "updated_at": iso}

A null field means "no override for this dimension" → the resolver falls back
to the normal min() across PG RatePolicies + defaults. This lets an admin
override only concurrency while leaving RPM on the default path (the common
case for power users who need more headroom on long-running streams).

No TTL: an override is an explicit operator configuration and must not
silently expire. Clearing is the admin's responsibility (DELETE endpoint or
PUT with null on both fields).

All functions degrade open: a Redis outage must never take down the data
plane, so reads return None and writes swallow errors. Consistent with
`upstream_health.filter_unhealthy` and the rest of the rate-limit layer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_KEY_PREFIX = "llm_gateway:rate:subject_override:"


@dataclass(frozen=True)
class SubjectRateOverride:
    """Effective per-subject override. None on a field = no override."""

    rpm: int | None
    concurrency: int | None

    @property
    def is_empty(self) -> bool:
        """True when neither dimension is overridden — equivalent to no key."""
        return self.rpm is None and self.concurrency is None


def _key(subject_id: UUID | str) -> str:
    return f"{_KEY_PREFIX}{subject_id}"


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    # Guard against non-positive overrides: a 0/negative limit would immediately
    # block every request, which is almost certainly an operator mistake rather
    # than intent. Treat such values as "no override" so the user is never
    # accidentally locked out by a typo.
    return result if result > 0 else None


def _payload(rpm: int | None, concurrency: int | None) -> str:
    return json.dumps(
        {
            "rpm": rpm,
            "concurrency": concurrency,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parse(raw: Any) -> SubjectRateOverride | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        loaded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(loaded, dict):
        return None
    return SubjectRateOverride(
        rpm=_coerce_int(loaded.get("rpm")),
        concurrency=_coerce_int(loaded.get("concurrency")),
    )


async def get_override(
    redis: Redis | None, subject_id: UUID | str
) -> SubjectRateOverride | None:
    """Read a single subject's override. None if unset or Redis unreachable.

    Degrades open: a Redis outage returns None so the resolver falls back to
    the normal PG-based path. The data plane must not fail because the override
    store is briefly unavailable.
    """
    if redis is None:
        return None
    try:
        raw = await redis.get(_key(subject_id))
    except RedisError:
        logger.exception("subject_rate_override_get_failed subject_id=%s", subject_id)
        return None
    return _parse(raw)


async def list_overrides(
    redis: Redis | None, subject_ids: list[UUID | str]
) -> dict[str, SubjectRateOverride]:
    """Batched read for the admin UI's user list.

    Returns a {str(subject_id): SubjectRateOverride} map containing only the
    subjects that actually have a non-empty override set. One MGET round-trip
    regardless of list length. Degrades open to an empty map on Redis failure.
    """
    if not subject_ids or redis is None:
        return {}
    keys = [_key(sid) for sid in subject_ids]
    try:
        values = await redis.mget(keys)
    except RedisError:
        logger.exception("subject_rate_override_list_failed")
        return {}
    out: dict[str, SubjectRateOverride] = {}
    for sid, raw in zip(subject_ids, values, strict=True):
        parsed = _parse(raw)
        if parsed is not None and not parsed.is_empty:
            out[str(sid)] = parsed
    return out


async def set_override(
    redis: Redis,
    subject_id: UUID | str,
    *,
    rpm: int | None,
    concurrency: int | None,
) -> SubjectRateOverride:
    """Write (replace) a subject's full override.

    PUT semantics: the entire payload is overwritten, so the caller must pass
    both fields. To change only one dimension, read-then-write the merged value.
    Non-positive values are coerced to None (treated as "no override" for that
    dimension) so a typo can never lock a user out.

    Empty overrides (both fields None) are deleted rather than stored, so the
    Redis keyset only ever holds real overrides — the admin list endpoint
    reflects exactly who has a custom limit.
    """
    coerced = SubjectRateOverride(
        rpm=_coerce_int(rpm), concurrency=_coerce_int(concurrency)
    )
    if coerced.is_empty:
        await clear_override(redis, subject_id)
        return coerced
    try:
        await redis.set(_key(subject_id), _payload(coerced.rpm, coerced.concurrency))
    except RedisError:
        # Re-raise on write: a silent failure here would leave the admin
        # believing the override was applied when it wasn't. The admin endpoint
        # surfaces this as a 5xx rather than a misleading success.
        logger.exception("subject_rate_override_set_failed subject_id=%s", subject_id)
        raise
    return coerced


async def clear_override(redis: Redis, subject_id: UUID | str) -> None:
    """Remove a subject's override entirely (both dimensions).

    After this the subject falls back to the normal RatePolicy resolution.
    Missing-key is a no-op. Degrades open: a Redis failure during clear is
    swallowed because the worst case is a stale override that the admin can
    retry — it must not block subject deletion or other cleanup.
    """
    try:
        await redis.delete(_key(subject_id))
    except RedisError:
        logger.exception("subject_rate_override_clear_failed subject_id=%s", subject_id)
