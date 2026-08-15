from datetime import datetime
from typing import Any

from fastapi import status as http_status

from llm_gateway.db.models import EndpointFamily, RequestOutcome, utcnow
from llm_gateway.services.facts_queue import enqueue_fact
from llm_gateway.services.security import AuthContext


def requested_model_alias(body: dict[str, Any]) -> str | None:
    model = body.get("model")
    return model if isinstance(model, str) else None


def outcome_for_http_status(status_code: int) -> RequestOutcome:
    if status_code == http_status.HTTP_429_TOO_MANY_REQUESTS:
        return RequestOutcome.RATE_LIMITED
    return RequestOutcome.POLICY_DENIAL


async def record_proxy_fact(
    *,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
    streaming: bool,
    outcome: RequestOutcome,
    endpoint: str,
    auth: AuthContext | None = None,
    route: Any | None = None,
    model_alias: str | None = None,
    upstream_target_id: Any | None = None,
    usage: dict[str, Any] | None = None,
    first_token_at: datetime | None = None,
    error_class: str | None = None,
    error_detail: str | None = None,
) -> None:
    if route is not None:
        model_alias = route.model_alias.alias
        upstream_target_id = route.upstream.id

    await enqueue_fact(
        {
            "request_id": request_id,
            "started_at": started_at,
            "ended_at": utcnow(),
            "endpoint_family": endpoint_family,
            "subject_id": auth.subject.id if auth else None,
            "subject_type": auth.subject.type if auth else None,
            "project_id": auth.project.id if auth else None,
            "model_alias": model_alias,
            "upstream_target_id": upstream_target_id,
            "streaming": streaming,
            "outcome": outcome,
            "usage": usage,
            "first_token_at": first_token_at,
            "error_class": error_class,
            "error_detail": error_detail,
        },
        endpoint=endpoint,
    )


async def record_proxy_error(
    *,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
    auth: AuthContext | None,
    route: Any | None,
    model_alias: str | None,
    streaming: bool,
    outcome: RequestOutcome,
    exc: Exception,
) -> None:
    await record_proxy_fact(
        request_id=request_id,
        started_at=started_at,
        endpoint_family=endpoint_family,
        auth=auth,
        route=route,
        model_alias=model_alias,
        streaming=streaming,
        outcome=outcome,
        usage=None,
        error_class=type(exc).__name__,
        error_detail=str(exc),
        endpoint=endpoint_family.value,
    )
