from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

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

    Mirrors the request construction of litellm_client.check_upstream_health
    (same base_url join, same header injection) but applies the stricter
    classify_health verdict used by the background checker.
    """
    # base_url 形如 "http://host:port/v1"，health_path 形如 "/models"。
    # 用字符串拼接保留与 check_upstream_health 完全一致的 URL 形态。
    url = upstream.base_url.rstrip("/") + "/" + upstream.health_path.lstrip("/")
    headers = dict(upstream.extra_headers or {})
    api_key = upstream.api_key_value or upstream.api_key_ref
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
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
                action="upstream.auto_disable",
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


def _quorum_breach(unhealthy_count: int, total: int, quorum_min: int) -> bool:
    """True when too many upstreams failed in one cycle to be a real outage.

    Cross-machine, cross-model upstreams failing in the same 3s window is the
    checker-side incident signature (event-loop freeze, network blip). When
    unhealthy_count >= quorum_min we skip batch-marking and emit a single
    quorum-failed audit row instead, so a frozen loop can no longer take out
    the whole fleet. quorum_min=2 means a single genuine failure still gets
    marked; only a suspicious batch is suppressed.
    """
    return total >= quorum_min and unhealthy_count >= quorum_min


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


async def _run_once(
    *,
    redis: Redis,
    timeout_seconds: float,
    unhealthy_ttl_seconds: int,
    quorum_min: int,
) -> None:
    """Probe every ACTIVE upstream concurrently; mark failures in Redis.

    Two-layer defense against the event-loop-freeze outage pattern:

    1. Quorum fuse: if ≥quorum_min upstreams fail in one cycle, treat it as a
       checker-side incident and skip ALL marking — never let a frozen loop
       take out the whole fleet. A single genuine failure (below quorum) is
       still marked.
    2. Redis TTL: every marker carries unhealthy_ttl_seconds, so a sidecar
       crash never wedges an upstream permanently — it auto-recovers.

    Probes run concurrently (asyncio.gather) so N replicas finish within one
    timeout window. Per-upstream marking errors are logged and swallowed so
    one Redis hiccup cannot abort the rest of the batch.
    """
    async with AsyncSessionLocal() as session:
        upstreams = await _collect_active_upstreams(session)

    if not upstreams:
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

    # Quorum fuse: suspicious batch → skip marking, emit one summary audit.
    if _quorum_breach(len(unhealthy), len(upstreams), quorum_min):
        logger.warning(
            "health_check_quorum_breach unhealthy=%d total=%d — skipping batch mark",
            len(unhealthy),
            len(upstreams),
        )
        await _record_quorum_failure(unhealthy)
        return

    # Healthy probes clear stale markers (auto-recovery). Unhealthy probes
    # below quorum set Redis markers + audit rows.
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
        except Exception:
            logger.exception(
                "health_check_mark_failed upstream_id=%s", upstream.id
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
    """Start the background health-check loop (no-op if disabled).

    Used by the sidecar process. The main gateway process no longer calls this
    — health checking lives in the sidecar so a main-process event-loop freeze
    can never produce a fleet-wide false-positive disable.
    """
    global _task
    if _task is not None:
        return
    if not _settings_enabled():
        logger.info("health_check_disabled_by_config")
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

    CancelledError re-raises so stop()'s await sees it cleanly; any other
    exception from a single iteration is logged and the loop continues — a
    transient Redis/DB hiccup must not kill the whole checker.
    """
    redis = _build_redis()
    try:
        while True:
            try:
                await _run_once(
                    redis=redis,
                    timeout_seconds=_settings_timeout(),
                    unhealthy_ttl_seconds=_settings_unhealthy_ttl(),
                    quorum_min=_settings_quorum_min(),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("health_check_iteration_failed")
            await asyncio.sleep(_settings_interval())
    finally:
        await redis.aclose()
