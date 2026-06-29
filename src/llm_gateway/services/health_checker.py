from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlmodel import col

from llm_gateway.db.models import ResourceState, UpstreamTarget
from llm_gateway.db.session import AsyncSessionLocal
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
    automatic disable.
    """
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
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


async def _disable_upstream(*, upstream_id, verdict: HealthVerdict) -> bool:
    """Set an ACTIVE upstream to DISABLED after a failed probe.

    Double-confirm state on read: if an admin disabled or restored the upstream
    between probe and commit, do nothing (no state overwrite, no duplicate
    audit). Returns True when the disable was applied.

    The <50ms window between probe-failure and this re-read is a known, accepted
    race: an admin who restores the node in that window may have their restore
    overwritten once. They retry to recover; the next probe cycle (3s later)
    will then re-evaluate. A version-based optimistic lock was judged not worth
    the complexity for such a narrow, self-recovering window.
    """
    async with AsyncSessionLocal() as session:
        upstream = await session.get(UpstreamTarget, upstream_id)
        if upstream is None or upstream.state != ResourceState.ACTIVE:
            return False
        upstream.state = ResourceState.DISABLED
        await record_audit_event(
            session,
            action="upstream.auto_disable",
            resource_type="upstream_target",
            resource_id=upstream.id,
            outcome="disabled",
            detail={
                "name": upstream.name,
                "health_path": upstream.health_path,
                "verdict": verdict.reason,
                "status_code": verdict.status_code,
            },
        )
        await session.commit()
        return True


async def _collect_active_upstreams(session) -> list:
    result = await session.execute(
        select(UpstreamTarget).where(
            col(UpstreamTarget.state) == ResourceState.ACTIVE
        )
    )
    return list(result.scalars().all())


async def _run_once(*, timeout_seconds: float) -> None:
    """Probe every ACTIVE upstream concurrently; disable the failures.

    Probes run concurrently (asyncio.gather) so N replicas finish within one
    timeout window. Disables are serial with per-upstream sessions so one
    failure cannot poison another — a RuntimeError in _disable_upstream is
    logged and swallowed so the remaining upstreams are still processed.
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

    for upstream, verdict in zip(upstreams, verdicts, strict=True):
        if verdict.healthy:
            continue
        try:
            await _disable_upstream(upstream_id=upstream.id, verdict=verdict)
        except Exception:
            logger.exception(
                "health_check_disable_failed upstream_id=%s", upstream.id
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


# --- Background task lifecycle ---


_task: asyncio.Task | None = None


async def start() -> None:
    """Start the background health-check loop (no-op if disabled).

    Fire-and-forget: schedules _main_loop and returns immediately. Idempotent —
    a second call while a task is running is ignored. Wired into app lifespan
    startup alongside ensure_builtin_identity.
    """
    global _task
    if _task is not None:
        return
    if not _settings_enabled():
        logger.info("health_check_disabled_by_config")
        return
    _task = asyncio.create_task(_main_loop())
    logger.info(
        "health_check_started interval=%.1fs timeout=%.1fs",
        _settings_interval(),
        _settings_timeout(),
    )


async def stop() -> None:
    """Cancel the background loop and wait for it to wind down.

    Safe to call when no task is running. Used by lifespan shutdown so a restart
    never leaves a dangling task probing /models.
    """
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
    transient DB hiccup must not kill the whole checker. Both interval and
    timeout are re-read each iteration (today both come from the lru_cached
    Settings singleton, but reading them here keeps them symmetric and lets a
    future hot-reloadable config take effect without a restart).
    """
    while True:
        try:
            await _run_once(timeout_seconds=_settings_timeout())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("health_check_iteration_failed")
        await asyncio.sleep(_settings_interval())
