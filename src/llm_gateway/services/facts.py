from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.db.models import (
    AuditEvent,
    EndpointFamily,
    RequestFact,
    RequestOutcome,
    SubjectType,
    UsageSource,
)


def extract_usage_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    try:
        return dict(value)
    except TypeError:
        return None


def token_from_usage(usage: dict[str, Any] | None, key: str) -> int | None:
    if not usage:
        return None
    value = usage.get(key)
    return value if isinstance(value, int) else None


def _nested_int(value: Any, *keys: str) -> int | None:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, int) else None


def _nested_float(value: Any, *keys: str) -> float | None:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, int | float):
        return float(current)
    return None


def _duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return max(0, round((ended_at - started_at).total_seconds() * 1000))


def prompt_tokens_from_usage(usage: dict[str, Any] | None) -> int | None:
    return token_from_usage(usage, "prompt_tokens") or token_from_usage(
        usage, "input_tokens"
    )


def completion_tokens_from_usage(usage: dict[str, Any] | None) -> int | None:
    return token_from_usage(usage, "completion_tokens") or token_from_usage(
        usage, "output_tokens"
    )


def total_tokens_from_usage(usage: dict[str, Any] | None) -> int | None:
    return token_from_usage(usage, "total_tokens") or token_from_usage(
        usage, "total_tokens_used"
    )


def cached_tokens_from_usage(usage: dict[str, Any] | None) -> int | None:
    return (
        token_from_usage(usage, "cached_tokens")
        or _nested_int(usage, "prompt_tokens_details", "cached_tokens")
        or _nested_int(usage, "input_tokens_details", "cached_tokens")
        or _nested_int(usage, "input_token_details", "cached_tokens")
        or token_from_usage(usage, "cache_read_input_tokens")
    )


def performance_int_from_usage(usage: dict[str, Any] | None, key: str) -> int | None:
    if not usage:
        return None
    return (
        token_from_usage(usage, key)
        or _nested_int(usage, "performance", key)
        or _nested_int(usage, "vllm", key)
    )


def performance_float_from_usage(
    usage: dict[str, Any] | None, key: str
) -> float | None:
    if not usage:
        return None
    value = usage.get(key)
    if isinstance(value, int | float):
        return float(value)
    return _nested_float(usage, "performance", key) or _nested_float(usage, "vllm", key)


async def record_request_fact(
    session: AsyncSession,
    *,
    request_id: str,
    started_at: datetime,
    ended_at: datetime,
    endpoint_family: EndpointFamily,
    subject_id,
    subject_type: SubjectType | None,
    project_id,
    model_alias: str | None,
    upstream_target_id,
    streaming: bool,
    outcome: RequestOutcome,
    usage: dict[str, Any] | None,
    first_token_at: datetime | None = None,
    retry_count: int = 0,
    fallback_count: int = 0,
    fallback_tokens: int | None = None,
    performance_detail: dict[str, Any] | None = None,
    error_class: str | None = None,
    error_detail: str | None = None,
) -> RequestFact:
    usage_source = UsageSource.LITELLM if usage else UsageSource.MISSING
    latency_ms = _duration_ms(started_at, ended_at)
    time_to_first_token_ms = (
        _duration_ms(started_at, first_token_at) if first_token_at else None
    )
    fact = RequestFact(
        request_id=request_id,
        started_at=started_at,
        ended_at=ended_at,
        endpoint_family=endpoint_family,
        subject_id=subject_id,
        subject_type=subject_type,
        project_id=project_id,
        model_alias=model_alias,
        upstream_target_id=upstream_target_id,
        streaming=streaming,
        outcome=outcome,
        usage_source=usage_source,
        prompt_tokens=prompt_tokens_from_usage(usage),
        completion_tokens=completion_tokens_from_usage(usage),
        total_tokens=total_tokens_from_usage(usage),
        cached_tokens=cached_tokens_from_usage(usage),
        latency_ms=latency_ms,
        time_to_first_token_ms=time_to_first_token_ms,
        stream_duration_ms=latency_ms if streaming else None,
        retry_count=retry_count,
        fallback_count=fallback_count,
        fallback_tokens=fallback_tokens,
        queue_ms=performance_int_from_usage(usage, "queue_ms"),
        prefill_ms=performance_int_from_usage(usage, "prefill_ms"),
        decode_ms=performance_int_from_usage(usage, "decode_ms"),
        kv_cache_usage=performance_float_from_usage(usage, "kv_cache_usage"),
        performance_detail=performance_detail or {},
        error_class=error_class,
        error_detail=error_detail[:1000] if error_detail else None,
    )
    session.add(fact)
    await session.flush()
    return fact


async def record_audit_event(
    session: AsyncSession,
    *,
    action: str,
    resource_type: str,
    outcome: str,
    actor_subject_id=None,
    resource_id=None,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_subject_id=actor_subject_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        detail=detail or {},
    )
    session.add(event)
    await session.flush()
    return event
