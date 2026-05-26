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


def prompt_tokens_from_usage(usage: dict[str, Any] | None) -> int | None:
    return token_from_usage(usage, "prompt_tokens") or token_from_usage(
        usage, "input_tokens"
    )


def completion_tokens_from_usage(usage: dict[str, Any] | None) -> int | None:
    return token_from_usage(usage, "completion_tokens") or token_from_usage(
        usage, "output_tokens"
    )


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
    error_class: str | None = None,
    error_detail: str | None = None,
) -> RequestFact:
    usage_source = UsageSource.LITELLM if usage else UsageSource.MISSING
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
        total_tokens=token_from_usage(usage, "total_tokens"),
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
