from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.api.deps import bearer_token, client_ip_dep, redis_dep, session_dep, settings_dep
from llm_gateway.core.config import Settings
from llm_gateway.db.models import EndpointFamily, RequestOutcome, utcnow
from llm_gateway.services.facts import extract_usage_dict, record_request_fact
from llm_gateway.services.litellm_client import (
    anthropic_messages_once,
    anthropic_messages_stream,
    completion_once,
    completion_stream,
)
from llm_gateway.services.policy import PolicyDenied, resolve_route_context
from llm_gateway.services.rate_limit import (
    RateLimitExceeded,
    check_request_rate,
    concurrency_slot,
    resolve_effective_rate_policy,
)
from llm_gateway.services.security import AuthContext, authenticate_gateway_key


router = APIRouter()


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return value
    return value


def _requested_model(body: dict[str, Any]) -> str:
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing_model")
    return model


async def _prepare(
    *,
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    auth: AuthContext,
    body: dict[str, Any],
    client_ip: str,
):
    try:
        rate_policy = await resolve_effective_rate_policy(
            session,
            key_id=auth.key.id,
            subject_id=auth.subject.id,
            project_id=auth.project.id,
            defaults=settings,
        )
        route = await resolve_route_context(
            session,
            auth=auth,
            requested_model=_requested_model(body),
            client_ip=client_ip,
        )
        await check_request_rate(
            redis,
            key_id=auth.key.id,
            limit=rate_policy.requests_per_minute,
        )
        return route, rate_policy
    except PolicyDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.reason) from exc
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc


@router.post("/v1/chat/completions")
async def openai_chat_completions(
    request: Request,
    session: AsyncSession = Depends(session_dep),
    redis: Redis = Depends(redis_dep),
    settings: Settings = Depends(settings_dep),
    client_ip: str = Depends(client_ip_dep),
):
    body = await request.json()
    streaming = bool(body.get("stream"))
    started_at = utcnow()
    request_id = request.headers.get("x-request-id") or str(uuid4())
    try:
        auth = await _authenticate_proxy_request(request, session)
    except HTTPException as exc:
        await _record_unauthenticated_request(
            session=session,
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            model_alias=body.get("model") if isinstance(body.get("model"), str) else None,
            exc=exc,
        )
        raise
    try:
        route, rate_policy = await _prepare(session=session, redis=redis, settings=settings, auth=auth, body=body, client_ip=client_ip)
    except HTTPException as exc:
        await _record_rejected_request(
            session=session,
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            auth=auth,
            model_alias=body.get("model") if isinstance(body.get("model"), str) else None,
            outcome=_outcome_for_http_exception(exc),
            exc=exc,
        )
        raise

    if streaming:
        return StreamingResponse(
            _stream_openai_response(
                session=session,
                redis=redis,
                settings=settings,
                auth=auth,
                route=route,
                concurrency_limit=rate_policy.concurrency_limit,
                body=body,
                started_at=started_at,
                request_id=request_id,
            ),
            media_type="text/event-stream",
        )

    try:
        async with concurrency_slot(
            redis,
            key_id=auth.key.id,
            limit=rate_policy.concurrency_limit,
        ):
            result = await completion_once(model_alias=route.model_alias, upstream=route.upstream, body=body)
        await record_request_fact(
            session,
            request_id=request_id,
            started_at=started_at,
            ended_at=utcnow(),
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            subject_id=auth.subject.id,
            subject_type=auth.subject.type,
            project_id=auth.project.id,
            model_alias=route.model_alias.alias,
            upstream_target_id=route.upstream.id,
            streaming=False,
            outcome=RequestOutcome.SUCCESS,
            usage=result.usage,
        )
        await session.commit()
        return JSONResponse(jsonable_encoder(_plain(result.response)))
    except Exception as exc:
        await _record_failure(
            session=session,
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            auth=auth,
            model_alias=route.model_alias.alias,
            upstream_target_id=route.upstream.id,
            streaming=False,
            outcome=RequestOutcome.ADAPTER_FAILURE,
            exc=exc,
        )
        return _error_response(status.HTTP_502_BAD_GATEWAY, "adapter_failure", exc)


async def _stream_openai_response(
    *,
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    auth: AuthContext,
    route,
    concurrency_limit: int,
    body: dict[str, Any],
    started_at: datetime,
    request_id: str,
):
    usage = None
    outcome = RequestOutcome.SUCCESS
    error: Exception | None = None
    try:
        async with concurrency_slot(
            redis,
            key_id=auth.key.id,
            limit=concurrency_limit,
        ):
            async for event, event_usage in completion_stream(
                model_alias=route.model_alias,
                upstream=route.upstream,
                body=body,
            ):
                usage = event_usage or usage
                yield event
    except Exception as exc:
        outcome = RequestOutcome.ADAPTER_FAILURE
        error = exc
        yield f"event: error\ndata: {str(exc)}\n\n"
    finally:
        await record_request_fact(
            session,
            request_id=request_id,
            started_at=started_at,
            ended_at=utcnow(),
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            subject_id=auth.subject.id,
            subject_type=auth.subject.type,
            project_id=auth.project.id,
            model_alias=route.model_alias.alias,
            upstream_target_id=route.upstream.id,
            streaming=True,
            outcome=outcome,
            usage=usage,
            error_class=type(error).__name__ if error else None,
            error_detail=str(error) if error else None,
        )
        await session.commit()


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    session: AsyncSession = Depends(session_dep),
    redis: Redis = Depends(redis_dep),
    settings: Settings = Depends(settings_dep),
    client_ip: str = Depends(client_ip_dep),
):
    body = await request.json()
    streaming = bool(body.get("stream"))
    started_at = utcnow()
    request_id = request.headers.get("x-request-id") or str(uuid4())
    try:
        auth = await _authenticate_proxy_request(request, session)
    except HTTPException as exc:
        await _record_unauthenticated_request(
            session=session,
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
            model_alias=body.get("model") if isinstance(body.get("model"), str) else None,
            exc=exc,
        )
        raise
    try:
        route, rate_policy = await _prepare(session=session, redis=redis, settings=settings, auth=auth, body=body, client_ip=client_ip)
    except HTTPException as exc:
        await _record_rejected_request(
            session=session,
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
            auth=auth,
            model_alias=body.get("model") if isinstance(body.get("model"), str) else None,
            outcome=_outcome_for_http_exception(exc),
            exc=exc,
        )
        raise

    if streaming:
        return StreamingResponse(
            _stream_anthropic_response(
                session=session,
                redis=redis,
                settings=settings,
                auth=auth,
                route=route,
                concurrency_limit=rate_policy.concurrency_limit,
                body=body,
                started_at=started_at,
                request_id=request_id,
            ),
            media_type="text/event-stream",
        )

    try:
        async with concurrency_slot(redis, key_id=auth.key.id, limit=rate_policy.concurrency_limit):
            result = await anthropic_messages_once(model_alias=route.model_alias, upstream=route.upstream, body=body)
        await record_request_fact(
            session,
            request_id=request_id,
            started_at=started_at,
            ended_at=utcnow(),
            endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
            subject_id=auth.subject.id,
            subject_type=auth.subject.type,
            project_id=auth.project.id,
            model_alias=route.model_alias.alias,
            upstream_target_id=route.upstream.id,
            streaming=False,
            outcome=RequestOutcome.SUCCESS,
            usage=result.usage,
        )
        await session.commit()
        return JSONResponse(jsonable_encoder(_plain(result.response)))
    except Exception as exc:
        await _record_failure(
            session=session,
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
            auth=auth,
            model_alias=route.model_alias.alias,
            upstream_target_id=route.upstream.id,
            streaming=False,
            outcome=RequestOutcome.ADAPTER_FAILURE,
            exc=exc,
        )
        return _error_response(status.HTTP_502_BAD_GATEWAY, "adapter_failure", exc)


async def _stream_anthropic_response(
    *,
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    auth: AuthContext,
    route,
    concurrency_limit: int,
    body: dict[str, Any],
    started_at: datetime,
    request_id: str,
):
    usage = None
    outcome = RequestOutcome.SUCCESS
    error: Exception | None = None
    try:
        async with concurrency_slot(redis, key_id=auth.key.id, limit=concurrency_limit):
            async for event, event_usage in anthropic_messages_stream(
                model_alias=route.model_alias,
                upstream=route.upstream,
                body=body,
            ):
                usage = event_usage or usage
                yield event
    except Exception as exc:
        outcome = RequestOutcome.ADAPTER_FAILURE
        error = exc
        yield f"event: error\ndata: {str(exc)}\n\n"
    finally:
        await record_request_fact(
            session,
            request_id=request_id,
            started_at=started_at,
            ended_at=utcnow(),
            endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
            subject_id=auth.subject.id,
            subject_type=auth.subject.type,
            project_id=auth.project.id,
            model_alias=route.model_alias.alias,
            upstream_target_id=route.upstream.id,
            streaming=True,
            outcome=outcome,
            usage=usage,
            error_class=type(error).__name__ if error else None,
            error_detail=str(error) if error else None,
        )
        await session.commit()


async def _record_failure(
    *,
    session: AsyncSession,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
    auth: AuthContext,
    model_alias: str,
    upstream_target_id,
    streaming: bool,
    outcome: RequestOutcome,
    exc: Exception,
) -> None:
    await record_request_fact(
        session,
        request_id=request_id,
        started_at=started_at,
        ended_at=utcnow(),
        endpoint_family=endpoint_family,
        subject_id=auth.subject.id,
        subject_type=auth.subject.type,
        project_id=auth.project.id,
        model_alias=model_alias,
        upstream_target_id=upstream_target_id,
        streaming=streaming,
        outcome=outcome,
        usage=None,
        error_class=type(exc).__name__,
        error_detail=str(exc),
    )
    await session.commit()


def _outcome_for_http_exception(exc: HTTPException) -> RequestOutcome:
    if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        return RequestOutcome.RATE_LIMITED
    return RequestOutcome.POLICY_DENIAL


def _error_response(status_code: int, error_class: str, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "type": error_class,
                "message": str(exc)[:1000],
            }
        },
    )


async def _authenticate_proxy_request(request: Request, session: AsyncSession) -> AuthContext:
    raw_key = bearer_token(request)
    context = await authenticate_gateway_key(session, raw_key)
    if not context:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_gateway_key")
    return context


async def _record_unauthenticated_request(
    *,
    session: AsyncSession,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
    model_alias: str | None,
    exc: HTTPException,
) -> None:
    await record_request_fact(
        session,
        request_id=request_id,
        started_at=started_at,
        ended_at=utcnow(),
        endpoint_family=endpoint_family,
        subject_id=None,
        subject_type=None,
        project_id=None,
        model_alias=model_alias,
        upstream_target_id=None,
        streaming=False,
        outcome=RequestOutcome.AUTH_FAILURE,
        usage=None,
        error_class=str(exc.status_code),
        error_detail=str(exc.detail),
    )
    await session.commit()


async def _record_rejected_request(
    *,
    session: AsyncSession,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
    auth: AuthContext,
    model_alias: str | None,
    outcome: RequestOutcome,
    exc: HTTPException,
) -> None:
    await record_request_fact(
        session,
        request_id=request_id,
        started_at=started_at,
        ended_at=utcnow(),
        endpoint_family=endpoint_family,
        subject_id=auth.subject.id,
        subject_type=auth.subject.type,
        project_id=auth.project.id,
        model_alias=model_alias,
        upstream_target_id=None,
        streaming=False,
        outcome=outcome,
        usage=None,
        error_class=str(exc.status_code),
        error_detail=str(exc.detail),
    )
    await session.commit()
