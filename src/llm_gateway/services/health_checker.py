from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx2 as httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlmodel import col

from llm_gateway.db.models import ResourceState, UpstreamTarget
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services import upstream_health
from llm_gateway.services.facts import record_audit_event


logger = logging.getLogger(__name__)

HEALTHY_STATUSES = frozenset({200, 404})


@dataclass(frozen=True)
class HealthVerdict:
    healthy: bool
    status_code: int | None
    reason: str


def classify_health(
    status_code: int | None, *, exc: Exception | None
) -> HealthVerdict:
    """Classify an upstream /models probe into a health verdict.

    200/404 are healthy (404 = 昇腾 PD 分离查不到 /models，明确是健康的).
    Any 5xx, network error, timeout, or non-404 4xx is unhealthy and triggers
    a Redis UNHEALTHY marker (which the route path filters out).

    Timeout is split into connect vs read so the audit log can distinguish a
    checker-side freeze (connect timeouts landing on the same tick — the
    event-loop-freeze signature) from a genuinely slow upstream (read timeout).
    """
    if exc is not None:
        if isinstance(exc, httpx.ConnectTimeout):
            return HealthVerdict(False, None, "connect_timeout")
        if isinstance(exc, httpx.ReadTimeout):
            return HealthVerdict(False, None, "read_timeout")
        if isinstance(exc, httpx.TimeoutException):
            # PoolTimeout / WriteTimeout / other narrow timeouts — keep distinct
            # from the connect/read split but still clearly a timeout class.
            return HealthVerdict(False, None, "timeout")
        if isinstance(exc, httpx.HTTPError):
            return HealthVerdict(False, None, "connection_error")
        return HealthVerdict(False, None, "unknown_error")
    if status_code in HEALTHY_STATUSES:
        return HealthVerdict(True, status_code, "ok")
    if status_code is None:
        # Defensive: a caller with neither an exception nor a status code gave
        # us nothing to classify — treat as unhealthy rather than raising on
        # the `>= 500` comparison below.
        return HealthVerdict(False, None, "unknown_error")
    if status_code >= 500:
        return HealthVerdict(False, status_code, "http_5xx")
    return HealthVerdict(False, status_code, "unexpected_status")


async def _probe_upstream(upstream, *, timeout_seconds: float) -> HealthVerdict:
    """GET {base_url}/{health_path} and classify the response.

    URL and headers come from litellm_client.probe_request_parts so the
    background prober and the admin manual Check hit the identical request;
    this function owns the STRICTER verdict (classify_health) used for
    automatic marking.
    """
    from llm_gateway.services.litellm_client import probe_request_parts

    url, headers = probe_request_parts(upstream)
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, headers=headers)
        return classify_health(response.status_code, exc=None)
    except Exception as exc:
        return classify_health(None, exc=exc)


async def _mark_unhealthy(
    redis: Redis,
    *,
    upstream_id,
    upstream_name: str,
    verdict: HealthVerdict,
    ttl_seconds: int,
) -> bool:
    """Record an UNHEALTHY marker in Redis + an audit row in PG.

    Unlike the old PG-state writer, this does NOT consult the current marker
    before setting it: the marker is idempotent (SET refreshes TTL) and the
    audit row is written every cycle so the operator can see ongoing failures.
    A passing probe clears the marker; TTL expiry is the dead-sidecar fallback.

    Audit is best-effort: a Redis success must not be rolled back if PG is
    briefly unavailable, because the routing path keys off Redis, not PG.
    """
    await upstream_health.mark_unhealthy(
        redis,
        upstream_id,
        reason=verdict.reason,
        status_code=verdict.status_code,
        ttl_seconds=ttl_seconds,
    )
    try:
        async with AsyncSessionLocal() as session:
            await record_audit_event(
                session,
                action="upstream.marked_unhealthy",
                resource_type="upstream_target",
                resource_id=upstream_id,
                outcome="unhealthy",
                detail={
                    "name": upstream_name,
                    "verdict": verdict.reason,
                    "status_code": verdict.status_code,
                },
            )
            await session.commit()
    except Exception:
        logger.exception(
            "health_check_audit_failed upstream_id=%s", upstream_id
        )
    return True


async def _clear_healthy(
    redis: Redis, *, upstream_id
) -> None:
    """Clear the UNHEALTHY marker on a passing probe (auto-recovery).

    No audit row for recovery: it's the normal steady state, and writing one
    per healthy probe per upstream would flood the audit log. The marker's
    absence is the recovery signal; the TTL is the dead-sidecar fallback.
    """
    await upstream_health.clear_unhealthy(redis, upstream_id)


async def _collect_active_upstreams(session) -> list:
    result = await session.execute(
        select(UpstreamTarget).where(
            col(UpstreamTarget.state) == ResourceState.ACTIVE
        )
    )
    return list(result.scalars().all())


# Failure reasons that could equally originate on the checker's side (frozen
# event loop, lost network) as on the upstream's. The complementary set —
# http_5xx / unexpected_status — proves the upstream answered, so those
# verdicts are always real and never subject to the quorum fuse.
_NETWORK_FAILURE_REASONS = frozenset(
    {"connect_timeout", "read_timeout", "timeout", "connection_error", "unknown_error"}
)


def _quorum_breach(
    unhealthy: list[tuple[object, HealthVerdict]], total: int, quorum_min: int
) -> bool:
    """True only for the checker-side outage signature.

    Suppress marking when EVERY probed upstream failed with a network-class
    reason and the batch is at least quorum_min: cross-machine, cross-model
    upstreams all failing that way in one 3s window is far more likely to be
    the checker itself (event-loop freeze, dead network) than the fleet, and a
    frozen loop must not be able to take out the whole fleet.

    Any cycle where at least one upstream answered — healthy, 5xx, or non-404
    4xx — proves the checker can still reach the fleet, so every unhealthy
    verdict in that cycle is real and gets marked, however many there are.
    This is the fix for the field failure where several genuinely dead
    endpoints were permanently suppressed by the old count-only quorum (≥2
    dead endpoints fleet-wide blocked ALL marking, cycle after cycle).
    """
    if total < quorum_min or len(unhealthy) < total:
        return False
    return all(verdict.reason in _NETWORK_FAILURE_REASONS for _, verdict in unhealthy)


async def _record_quorum_failure(
    unhealthy: list[tuple[object, HealthVerdict]]
) -> None:
    """Emit one audit row summarizing a quorum-failed cycle.

    Best-effort: a PG outage during a checker-side incident must not mask the
    quorum decision (which has already been made and acted on — skipping marks).
    """
    try:
        async with AsyncSessionLocal() as session:
            await record_audit_event(
                session,
                action="upstream.health_check_quorum_failed",
                resource_type="upstream_target",
                resource_id=None,
                outcome="skipped",
                detail={
                    "unhealthy_count": len(unhealthy),
                    "upstreams": [
                        {
                            "id": str(u.id),
                            "name": getattr(u, "name", None),
                            "verdict": v.reason,
                            "status_code": v.status_code,
                        }
                        for u, v in unhealthy
                    ],
                },
            )
            await session.commit()
    except Exception:
        logger.exception("health_check_quorum_audit_failed")


# Per-cycle liveness heartbeat. "Is the sidecar actually probing?" becomes a
# single Redis GET instead of an inference from missing auto-disables — the
# original failure mode where a not-running sidecar was indistinguishable from
# a healthy fleet. No TTL: the `at` timestamp lets readers judge staleness, and
# a dead sidecar leaves an honest "last cycle: long ago" signal behind.
_HEARTBEAT_KEY = "llm_gateway:health_check:last_cycle"


async def _write_heartbeat(
    redis: Redis,
    *,
    total: int,
    unhealthy: int,
    marked: int,
    suppressed: bool,
) -> None:
    payload = json.dumps(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "unhealthy": unhealthy,
            "marked": marked,
            "suppressed": suppressed,
        }
    )
    try:
        await redis.set(_HEARTBEAT_KEY, payload)
    except Exception:
        logger.exception("health_check_heartbeat_failed")


async def read_last_cycle(redis: Redis | None) -> dict | None:
    """Read the sidecar's last-cycle heartbeat, or None if it never ran.

    Used by the admin health-check endpoint so the UI can show "last probe
    cycle: Ns ago" — or "sidecar not reporting" when the key is missing/stale.
    """
    if redis is None:
        return None
    try:
        raw = await redis.get(_HEARTBEAT_KEY)
    except Exception:
        logger.exception("health_check_heartbeat_read_failed")
        return None
    return upstream_health.parse_payload(raw)


async def _run_once(
    *,
    redis: Redis,
    timeout_seconds: float,
    unhealthy_ttl_seconds: int,
    quorum_min: int,
) -> None:
    """Probe every ACTIVE upstream concurrently; mark failures in Redis.

    Two-layer defense against the event-loop-freeze outage pattern:

    1. Quorum fuse: when the WHOLE batch fails with network-class reasons, the
       failure is attributed to the checker and no marking happens — a frozen
       loop must not take out the fleet. Partial failures (any upstream
       answered) are always real and always marked.
    2. Redis TTL: every marker carries unhealthy_ttl_seconds, so a sidecar
       crash never wedges an upstream permanently — it auto-recovers.

    Probes run concurrently (asyncio.gather) so N replicas finish within one
    timeout window. Per-upstream marking errors are logged and swallowed so
    one Redis hiccup cannot abort the rest of the batch.
    """
    async with AsyncSessionLocal() as session:
        upstreams = await _collect_active_upstreams(session)

    if not upstreams:
        await _write_heartbeat(
            redis, total=0, unhealthy=0, marked=0, suppressed=False
        )
        return

    verdicts = await asyncio.gather(
        *[
            _probe_upstream(upstream, timeout_seconds=timeout_seconds)
            for upstream in upstreams
        ]
    )

    unhealthy: list[tuple[object, HealthVerdict]] = [
        (u, v) for u, v in zip(upstreams, verdicts, strict=True) if not v.healthy
    ]

    # Quorum fuse: checker-side outage signature → skip marking, emit one
    # summary audit.
    if _quorum_breach(unhealthy, len(upstreams), quorum_min):
        logger.warning(
            "health_check_quorum_breach unhealthy=%d total=%d — skipping batch mark",
            len(unhealthy),
            len(upstreams),
        )
        await _record_quorum_failure(unhealthy)
        await _write_heartbeat(
            redis,
            total=len(upstreams),
            unhealthy=len(unhealthy),
            marked=0,
            suppressed=True,
        )
        return

    # Healthy probes clear stale markers (auto-recovery). Unhealthy probes
    # set Redis markers + audit rows.
    marked = 0
    for upstream, verdict in zip(upstreams, verdicts, strict=True):
        if verdict.healthy:
            try:
                await _clear_healthy(redis, upstream_id=upstream.id)
            except Exception:
                logger.exception(
                    "health_check_clear_failed upstream_id=%s", upstream.id
                )
            continue
        try:
            await _mark_unhealthy(
                redis,
                upstream_id=upstream.id,
                upstream_name=upstream.name,
                verdict=verdict,
                ttl_seconds=unhealthy_ttl_seconds,
            )
            marked += 1
        except Exception:
            logger.exception(
                "health_check_mark_failed upstream_id=%s", upstream.id
            )
    await _write_heartbeat(
        redis,
        total=len(upstreams),
        unhealthy=len(unhealthy),
        marked=marked,
        suppressed=False,
    )


# --- Settings accessors (lazy import to avoid module-import-time config load) ---


def _settings_enabled() -> bool:
    from llm_gateway.core.config import get_settings

    return get_settings().health_check_enabled


def _settings_interval() -> float:
    from llm_gateway.core.config import get_settings

    return get_settings().health_check_interval_seconds


def _settings_timeout() -> float:
    from llm_gateway.core.config import get_settings

    return get_settings().health_check_timeout_seconds


def _settings_unhealthy_ttl() -> int:
    from llm_gateway.core.config import get_settings

    return get_settings().health_check_unhealthy_ttl_seconds


def _settings_quorum_min() -> int:
    from llm_gateway.core.config import get_settings

    return get_settings().health_check_quorum_min


# --- Runtime enable override (Redis) ---
#
# The env-var default (health_check_enabled) is frozen at sidecar start by the
# lru_cached Settings singleton. A Redis override key lets an admin toggle the
# checker at runtime without restarting the sidecar: SET "0" to force-disable
# (emergency stop), DEL to withdraw the override and fall back to the env
# default. The sidecar re-reads this every cycle in _main_loop, so a toggle
# takes effect within one interval (≤3s).

_ENABLED_OVERRIDE_KEY = "llm_gateway:health_check:enabled"
_DISABLED_SENTINEL = "0"


async def is_enabled_override(redis: Redis) -> bool | None:
    """Return the runtime override state, or None if no override is set.

    True = forced enabled, False = forced disabled, None = no override (use
    the env-var default). Today only the disable sentinel is written, so the
    return is False or None; True is reserved for a future "force-on even when
    env says off" if that need arises.
    """
    value = await redis.get(_ENABLED_OVERRIDE_KEY)
    if value is None:
        return None
    return value != _DISABLED_SENTINEL


async def set_enabled_override(redis: Redis, enabled: bool) -> None:
    """Set the runtime override. enabled=False forces the checker off."""
    if enabled:
        await redis.delete(_ENABLED_OVERRIDE_KEY)
    else:
        await redis.set(_ENABLED_OVERRIDE_KEY, _DISABLED_SENTINEL)


async def effective_enabled(redis: Redis) -> tuple[bool, str]:
    """Resolve the effective enabled state + its source.

    Returns (enabled, source) where source is "redis_override" or "env_default".
    Redis override takes precedence over the env-var default.
    """
    override = await is_enabled_override(redis)
    if override is not None:
        return override, "redis_override"
    return _settings_enabled(), "env_default"


def _build_redis() -> Redis:
    """Construct a process-local Redis client for the sidecar.

    The sidecar runs in its own process with its own GIL; this client is
    independent of the main process's redis_client singleton. Both point at the
    same Redis instance (same LLM_GATEWAY_REDIS_URL) so markers written here are
    visible to the routing path in the main process.
    """
    from llm_gateway.core.config import get_settings
    from llm_gateway.services.rate_limit import create_redis

    return create_redis(get_settings())


# --- Background task lifecycle (used by the sidecar entrypoint) ---


_task: asyncio.Task | None = None


async def start() -> None:
    """Start the background health-check loop.

    Used by the sidecar process. The main gateway process no longer calls this
    — health checking lives in the sidecar so a main-process event-loop freeze
    can never produce a fleet-wide false-positive disable.

    Always starts the loop task regardless of the env-var default: the loop
    re-checks effective_enabled (Redis override > env default) each cycle, so
    an admin toggle takes effect within one interval without a sidecar restart.
    If both env default and Redis say disabled, the loop just sleeps without
    probing — cheap, and ready to resume instantly when re-enabled.
    """
    global _task
    if _task is not None:
        return
    _task = asyncio.create_task(_main_loop())
    logger.info(
        "health_check_started interval=%.1fs timeout=%.1fs unhealthy_ttl=%ds quorum_min=%d",
        _settings_interval(),
        _settings_timeout(),
        _settings_unhealthy_ttl(),
        _settings_quorum_min(),
    )


async def stop() -> None:
    """Cancel the background loop and wait for it to wind down."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("health_check_loop_error")
    _task = None
    logger.info("health_check_stopped")


async def _main_loop() -> None:
    """Run _run_once every interval until cancelled.

    Each cycle resolves effective_enabled (Redis override > env default) so an
    admin toggle takes effect within one interval without restarting the
    sidecar. When disabled the loop sleeps without probing — cheap, and ready
    to resume instantly when re-enabled.

    CancelledError re-raises so stop()'s await sees it cleanly; any other
    exception from a single iteration is logged and the loop continues — a
    transient Redis/DB hiccup must not kill the whole checker.
    """
    redis = _build_redis()
    try:
        while True:
            try:
                enabled, source = await effective_enabled(redis)
                if enabled:
                    await _run_once(
                        redis=redis,
                        timeout_seconds=_settings_timeout(),
                        unhealthy_ttl_seconds=_settings_unhealthy_ttl(),
                        quorum_min=_settings_quorum_min(),
                    )
                else:
                    logger.debug("health_check_skipped (disabled, source=%s)", source)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("health_check_iteration_failed")
            await asyncio.sleep(_settings_interval())
    finally:
        await redis.aclose()
