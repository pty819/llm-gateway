"""Per-team, per-member, time-windowed token quotas ("coding plan" style).

Semantics (see README「权限组分时段 Token 配额」):

- A team's configured limit applies to EACH MEMBER INDIVIDUALLY: setting
  早/午/晚 = 50M means every member of the team gets their own 50M budget
  per window. Members do not share a pool.
- Windows, evaluated in ``Settings.quota_timezone``:
  morning [08:00, 13:00), afternoon [13:00, 18:00), evening [18:00, next
  08:00). The evening window belongs to the date it starts on, so its counter
  key stays stable when the clock crosses midnight.
- A member's candidate set for a request = teams where the subject has an
  ACTIVE membership, the team is ACTIVE, the team holds an ACTIVE
  ModelTeamGrant for the requested model, and the team has an ACTIVE quota
  row with a non-NULL limit for the current window.
- Admission is check-any: the request passes while ANY candidate pool still
  has remaining budget. Charging is charge-all: the request's actual tokens
  are deducted from the member's counter in EVERY candidate team. A member
  of quota'd teams A (400) and B (500) therefore enjoys the larger budget:
  they can spend up to 500 in total across the window.
- Counters are check-before-charge, so concurrent in-flight requests can
  overshoot a limit by up to the tokens of the overlapping requests. That is
  the standard trade-off for admission-time quota and is accepted here.
- Token accounting uses total tokens (prompt + completion, cache hits
  included), matching request_facts.total_tokens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.core.config import get_settings
from llm_gateway.db.models import (
    ModelTeamGrant,
    ResourceState,
    Team,
    TeamMembership,
    TeamTokenQuota,
)
from llm_gateway.services.facts import (
    completion_tokens_from_usage,
    prompt_tokens_from_usage,
    total_tokens_from_usage,
)
from llm_gateway.services.rate_limit import RateLimitExceeded

logger = logging.getLogger("llm_gateway.team_quota")

WINDOW_MORNING = "morning"
WINDOW_AFTERNOON = "afternoon"
WINDOW_EVENING = "evening"

_QUOTA_KEY_PREFIX = "llm_gateway:quota:tokens"
# Observation grace beyond the window end: counters stop being consulted the
# moment the window rolls over, the extra TTL only keeps them inspectable.
_WINDOW_END_GRACE_SECONDS = 3600

_LIMIT_COLUMN_BY_WINDOW = {
    WINDOW_MORNING: TeamTokenQuota.morning_tokens,
    WINDOW_AFTERNOON: TeamTokenQuota.afternoon_tokens,
    WINDOW_EVENING: TeamTokenQuota.evening_tokens,
}


@dataclass(frozen=True)
class TeamQuotaContext:
    """Admission-time snapshot of the pools a request may draw from.

    ``subject_id`` scopes every counter: the same team's limit is tracked
    separately for each member (per-member budgets, not a shared pool)."""

    subject_id: UUID
    window: str
    window_date: date
    window_end: datetime  # aware, quota timezone
    pools: list[tuple[UUID, int]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.pools


def quota_timezone() -> ZoneInfo:
    name = get_settings().quota_timezone
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("invalid quota_timezone %r, falling back to UTC", name)
        return timezone.utc


def current_window(now: datetime | None = None, *, tz: ZoneInfo | None = None):
    """Return (window, window_date, window_end) for ``now`` in the quota tz.

    ``now`` is an aware datetime (any timezone); it is converted to the quota
    timezone before the boundaries are applied.
    """
    tz = tz or quota_timezone()
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    morning_start = time(8, 0)
    afternoon_start = time(13, 0)
    evening_start = time(18, 0)

    if local.time() < morning_start:
        # [00:00, 08:00) is the tail of the previous day's evening window.
        window = WINDOW_EVENING
        start = datetime.combine(local.date() - timedelta(days=1), evening_start, tzinfo=tz)
        end = datetime.combine(local.date(), morning_start, tzinfo=tz)
    elif local.time() < afternoon_start:
        window = WINDOW_MORNING
        start = datetime.combine(local.date(), morning_start, tzinfo=tz)
        end = datetime.combine(local.date(), afternoon_start, tzinfo=tz)
    elif local.time() < evening_start:
        window = WINDOW_AFTERNOON
        start = datetime.combine(local.date(), afternoon_start, tzinfo=tz)
        end = datetime.combine(local.date(), evening_start, tzinfo=tz)
    else:
        window = WINDOW_EVENING
        start = datetime.combine(local.date(), evening_start, tzinfo=tz)
        end = datetime.combine(local.date() + timedelta(days=1), morning_start, tzinfo=tz)
    return window, start.date(), end


async def resolve_team_quota(
    session: AsyncSession,
    *,
    subject_id: UUID,
    model_alias_id: UUID,
    now: datetime | None = None,
) -> TeamQuotaContext | None:
    """Snapshot the quota pools that apply to this request, or None when the
    feature does not constrain it (subject in no quota'd team granting the
    model). Returns plain data only — no ORM objects escape the session."""
    window, window_date, window_end = current_window(now)
    limit_column = _LIMIT_COLUMN_BY_WINDOW[window]
    result = await session.execute(
        select(col(TeamTokenQuota.team_id), col(limit_column))
        .join(Team, col(Team.id) == col(TeamTokenQuota.team_id))
        .join(
            TeamMembership,
            (col(TeamMembership.team_id) == col(Team.id))
            & (col(TeamMembership.subject_id) == subject_id),
        )
        .join(
            ModelTeamGrant,
            (col(ModelTeamGrant.team_id) == col(Team.id))
            & (col(ModelTeamGrant.model_alias_id) == model_alias_id),
        )
        .where(
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(ModelTeamGrant.state) == ResourceState.ACTIVE,
            col(TeamTokenQuota.state) == ResourceState.ACTIVE,
            col(limit_column).is_not(None),
        )
    )
    pools = [(UUID(str(team_id)), int(limit)) for team_id, limit in result.all()]
    if not pools:
        return None
    return TeamQuotaContext(
        subject_id=subject_id,
        window=window,
        window_date=window_date,
        window_end=window_end,
        pools=pools,
    )


def counter_key(team_id: UUID, subject_id: UUID, window: str, window_date: date) -> str:
    """Redis counter key for one member's budget in one team's window. Shared
    by the data plane (check/charge) and the admin usage display so the format
    can never drift between them."""
    return (
        f"{_QUOTA_KEY_PREFIX}:{team_id}:{subject_id}:{window}:{window_date:%Y%m%d}"
    )


def _counter_key(quota: TeamQuotaContext, team_id: UUID) -> str:
    return counter_key(team_id, quota.subject_id, quota.window, quota.window_date)


async def check_team_quota(redis: Redis, quota: TeamQuotaContext) -> None:
    """Raise RateLimitExceeded when every candidate pool is exhausted."""
    if quota.empty:
        return
    try:
        used_values = await redis.mget(
            [_counter_key(quota, team_id) for team_id, _ in quota.pools]
        )
    except RedisError:
        # Mirror check_request_rate's degradation policy so quota enforcement
        # and rate limiting fail the same way when Redis is down.
        if get_settings().rate_limit_fail_closed:
            raise RateLimitExceeded("team_token_quota_unavailable") from None
        return
    for (_, limit), used in zip(quota.pools, used_values, strict=True):
        if used is None or int(used) < limit:
            return
    raise RateLimitExceeded("team_token_quota_exceeded")


async def charge_team_quota(redis: Redis, quota: TeamQuotaContext, tokens: int) -> None:
    """Deduct ``tokens`` from every candidate pool. Best-effort: a Redis
    failure is logged and skipped — charging must never break the response
    tail, and request_facts remains the reconciliation source of truth."""
    if tokens <= 0 or quota.empty:
        return
    ttl = max(
        60, int((quota.window_end - datetime.now(quota.window_end.tzinfo)).total_seconds())
        + _WINDOW_END_GRACE_SECONDS
    )
    for team_id, _limit in quota.pools:
        key = _counter_key(quota, team_id)
        try:
            used = await redis.incrby(key, tokens)
            if used == tokens:
                await redis.expire(key, ttl)
        except RedisError:
            logger.warning(
                "team quota charge failed (team=%s window=%s tokens=%s)",
                team_id,
                quota.window,
                tokens,
            )


def token_total_from_usage(usage: dict[str, Any] | None) -> int:
    """total_tokens with a prompt+completion fallback; 0 when unknown."""
    total = total_tokens_from_usage(usage)
    if total is not None:
        return int(total)
    prompt = prompt_tokens_from_usage(usage) or 0
    completion = completion_tokens_from_usage(usage) or 0
    return int(prompt + completion)
