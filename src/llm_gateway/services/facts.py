from datetime import datetime
from typing import Any
import contextvars

from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.db.models import (
    AuditEvent,
    EndpointFamily,
    RequestFact,
    RequestOutcome,
    SubjectType,
    UsageSource,
)


# Request-scoped actor for admin audit events. Set by the admin dependency
# (session-based admin actions record the human subject; token-based system
# actions leave it unset). Read as the default actor_subject_id so individual
# record_audit_event call sites do not need to thread the actor manually.
admin_actor_subject_id: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "admin_actor_subject_id", default=None
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
    error_class: str | None = None,
    error_detail: str | None = None,
) -> RequestFact:
    # retry/fallback telemetry columns exist in the schema but have no
    # producer today (the native router does not surface retry counts), so
    # they are intentionally not written here; analytics sums them as 0.
    # usage_source IS written: tests assert it and it distinguishes
    # "no usage reported" from "usage missing/zero".
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
        queue_ms=performance_int_from_usage(usage, "queue_ms"),
        prefill_ms=performance_int_from_usage(usage, "prefill_ms"),
        decode_ms=performance_int_from_usage(usage, "decode_ms"),
        kv_cache_usage=performance_float_from_usage(usage, "kv_cache_usage"),
        error_class=error_class,
        error_detail=error_detail[:1000] if error_detail else None,
    )
    session.add(fact)
    await session.flush()
    return fact


_AUDIT_SENSITIVE_KEYS = frozenset(
    {
        "api_key_value",
        "api_key_ref",
        "password",
        "token_hash",
        "key_hash",
        "authorization",
        "x-api-key",
        "api-key",
        "apikey",
        "bearer",
        "cookie",
        "secret",
        "env",
        "headers",
    }
)


def _redact_audit_detail(value: Any) -> Any:
    """Strip secret values from audit detail before persistence. Audit events are
    append-only history readable by every admin, so an upstream API key or
    password rotated via an update must never land in ``audit_events.detail``.
    """
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if key.lower() in _AUDIT_SENSITIVE_KEYS
                else _redact_audit_detail(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_audit_detail(item) for item in value]
    return value


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
    if actor_subject_id is None:
        actor_subject_id = admin_actor_subject_id.get()
    event = AuditEvent(
        actor_subject_id=actor_subject_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        detail=_redact_audit_detail(detail) or {},
    )
    session.add(event)
    await session.flush()
    return event
