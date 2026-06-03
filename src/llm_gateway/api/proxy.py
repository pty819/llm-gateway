import asyncio
from contextlib import suppress
from datetime import datetime
from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.api.deps import (
    bearer_token,
    client_ip_dep,
    redis_dep,
    settings_dep,
)
from llm_gateway.core.config import Settings
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.db.models import EndpointFamily, RequestOutcome, utcnow
from llm_gateway.services.facts_queue import enqueue_fact
from llm_gateway.services.litellm_client import (
    anthropic_messages_once,
    anthropic_messages_stream,
    completion_once,
    completion_stream,
    responses_once,
    responses_stream,
)
from llm_gateway.services.policy import (
    PolicyDenied,
    list_accessible_model_aliases,
    resolve_route_context,
)
from llm_gateway.services.rate_limit import (
    RateLimitExceeded,
    acquire_concurrency_slot,
    check_request_rate,
    concurrency_slot,
    release_concurrency_slot,
    resolve_effective_rate_policy,
)
from llm_gateway.services.runtime_metrics import (
    mark_connection_closed,
    mark_connection_open,
    route_info,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="missing_model"
        )
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=exc.reason
        ) from exc
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc


async def _resolve_proxy_context(
    *,
    request: Request,
    redis: Redis,
    settings: Settings,
    body: dict[str, Any],
    client_ip: str,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
):
    async with AsyncSessionLocal() as session:
        try:
            auth = await _authenticate_proxy_request(request, session)
        except HTTPException as exc:
            await _record_unauthenticated_request(
                request_id=request_id,
                started_at=started_at,
                endpoint_family=endpoint_family,
                model_alias=body.get("model")
                if isinstance(body.get("model"), str)
                else None,
                exc=exc,
            )
            await session.rollback()
            raise

        try:
            route, rate_policy = await _prepare(
                session=session,
                redis=redis,
                settings=settings,
                auth=auth,
                body=body,
                client_ip=client_ip,
            )
        except HTTPException as exc:
            await _record_rejected_request(
                request_id=request_id,
                started_at=started_at,
                endpoint_family=endpoint_family,
                auth=auth,
                model_alias=body.get("model")
                if isinstance(body.get("model"), str)
                else None,
                outcome=_outcome_for_http_exception(exc),
                exc=exc,
            )
            await session.rollback()
            raise

        _detach_proxy_context(session, auth, route)
        await session.rollback()
        return auth, route, rate_policy


def _detach_proxy_context(session: AsyncSession, auth: AuthContext, route) -> None:
    for item in (
        auth.key,
        auth.subject,
        auth.project,
        route.model_alias,
        route.upstream,
    ):
        with suppress(Exception):
            session.expunge(item)


@router.post("/v1/chat/completions")
async def openai_chat_completions(
    request: Request,
    redis: Redis = Depends(redis_dep),
    settings: Settings = Depends(settings_dep),
    client_ip: str = Depends(client_ip_dep),
):
    body = await request.json()
    streaming = bool(body.get("stream"))
    started_at = utcnow()
    request_id = request.headers.get("x-request-id") or str(uuid4())
    auth, route, rate_policy = await _resolve_proxy_context(
        request=request,
        redis=redis,
        settings=settings,
        body=body,
        client_ip=client_ip,
        request_id=request_id,
        started_at=started_at,
        endpoint_family=EndpointFamily.OPENAI_CHAT,
    )

    if streaming:
        concurrency_key = await _acquire_streaming_concurrency(
            redis=redis,
            auth=auth,
            route=route,
            rate_policy=rate_policy,
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.OPENAI_CHAT,
        )
        return StreamingResponse(
            _stream_openai_response(
                redis=redis,
                auth=auth,
                route=route,
                concurrency_key=concurrency_key,
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
            metrics_member = await _mark_runtime_connection_open(
                redis=redis, request_id=request_id, route=route
            )
            try:
                result = await completion_once(
                    model_alias=route.model_alias, upstream=route.upstream, body=body
                )
            finally:
                await _mark_runtime_connection_closed(redis, metrics_member)
        await enqueue_fact(
            {
                "request_id": request_id,
                "started_at": started_at,
                "ended_at": utcnow(),
                "endpoint_family": EndpointFamily.OPENAI_CHAT,
                "subject_id": auth.subject.id,
                "subject_type": auth.subject.type,
                "project_id": auth.project.id,
                "model_alias": route.model_alias.alias,
                "upstream_target_id": route.upstream.id,
                "streaming": False,
                "outcome": RequestOutcome.SUCCESS,
                "usage": result.usage,
            },
            endpoint="chat_completions",
        )
        return JSONResponse(jsonable_encoder(_plain(result.response)))
    except RateLimitExceeded as exc:
        await _raise_rate_limited_after_route(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            auth=auth,
            streaming=False,
            route=route,
            exc=exc,
        )
    except Exception as exc:
        await _record_failure(
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
    redis: Redis,
    auth: AuthContext,
    route,
    concurrency_key: str,
    body: dict[str, Any],
    started_at: datetime,
    request_id: str,
):
    usage = None
    first_token_at: datetime | None = None
    outcome = RequestOutcome.SUCCESS
    error: BaseException | None = None
    metrics_member = await _mark_runtime_connection_open(
        redis=redis, request_id=request_id, route=route
    )
    try:
        async for event, event_usage in completion_stream(
            model_alias=route.model_alias,
            upstream=route.upstream,
            body=body,
        ):
            if first_token_at is None:
                first_token_at = utcnow()
            usage = event_usage or usage
            yield event
    except asyncio.CancelledError as exc:
        outcome = RequestOutcome.CLIENT_CANCELLED
        error = exc
        raise
    except Exception as exc:
        outcome = RequestOutcome.ADAPTER_FAILURE
        error = exc
        yield f"event: error\ndata: {str(exc)}\n\n"
    finally:
        await _mark_runtime_connection_closed(redis, metrics_member)
        with suppress(Exception):
            await release_concurrency_slot(redis, concurrency_key)
        await enqueue_fact(
            {
                "request_id": request_id,
                "started_at": started_at,
                "ended_at": utcnow(),
                "endpoint_family": EndpointFamily.OPENAI_CHAT,
                "subject_id": auth.subject.id,
                "subject_type": auth.subject.type,
                "project_id": auth.project.id,
                "model_alias": route.model_alias.alias,
                "upstream_target_id": route.upstream.id,
                "streaming": True,
                "outcome": outcome,
                "usage": usage,
                "first_token_at": first_token_at,
                "error_class": type(error).__name__ if error else None,
                "error_detail": str(error) if error else None,
            },
            endpoint="stream_openai",
        )


@router.post("/v1/responses")
async def openai_responses(
    request: Request,
    redis: Redis = Depends(redis_dep),
    settings: Settings = Depends(settings_dep),
    client_ip: str = Depends(client_ip_dep),
):
    body = await request.json()
    streaming = bool(body.get("stream"))
    started_at = utcnow()
    request_id = request.headers.get("x-request-id") or str(uuid4())
    auth, route, rate_policy = await _resolve_proxy_context(
        request=request,
        redis=redis,
        settings=settings,
        body=body,
        client_ip=client_ip,
        request_id=request_id,
        started_at=started_at,
        endpoint_family=EndpointFamily.OPENAI_RESPONSES,
    )

    if streaming:
        concurrency_key = await _acquire_streaming_concurrency(
            redis=redis,
            auth=auth,
            route=route,
            rate_policy=rate_policy,
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.OPENAI_RESPONSES,
        )
        return StreamingResponse(
            _stream_responses(
                redis=redis,
                auth=auth,
                route=route,
                concurrency_key=concurrency_key,
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
            metrics_member = await _mark_runtime_connection_open(
                redis=redis, request_id=request_id, route=route
            )
            try:
                result = await responses_once(
                    model_alias=route.model_alias, upstream=route.upstream, body=body
                )
            finally:
                await _mark_runtime_connection_closed(redis, metrics_member)
        await enqueue_fact(
            {
                "request_id": request_id,
                "started_at": started_at,
                "ended_at": utcnow(),
                "endpoint_family": EndpointFamily.OPENAI_RESPONSES,
                "subject_id": auth.subject.id,
                "subject_type": auth.subject.type,
                "project_id": auth.project.id,
                "model_alias": route.model_alias.alias,
                "upstream_target_id": route.upstream.id,
                "streaming": False,
                "outcome": RequestOutcome.SUCCESS,
                "usage": result.usage,
            },
            endpoint="responses",
        )
        return JSONResponse(jsonable_encoder(_plain(result.response)))
    except RateLimitExceeded as exc:
        await _raise_rate_limited_after_route(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.OPENAI_RESPONSES,
            auth=auth,
            streaming=False,
            route=route,
            exc=exc,
        )
    except Exception as exc:
        await _record_failure(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.OPENAI_RESPONSES,
            auth=auth,
            model_alias=route.model_alias.alias,
            upstream_target_id=route.upstream.id,
            streaming=False,
            outcome=RequestOutcome.ADAPTER_FAILURE,
            exc=exc,
        )
        return _error_response(status.HTTP_502_BAD_GATEWAY, "adapter_failure", exc)


async def _stream_responses(
    *,
    redis: Redis,
    auth: AuthContext,
    route,
    concurrency_key: str,
    body: dict[str, Any],
    started_at: datetime,
    request_id: str,
):
    usage = None
    first_token_at: datetime | None = None
    outcome = RequestOutcome.SUCCESS
    error: BaseException | None = None
    metrics_member = await _mark_runtime_connection_open(
        redis=redis, request_id=request_id, route=route
    )
    try:
        async for event, event_usage in responses_stream(
            model_alias=route.model_alias,
            upstream=route.upstream,
            body=body,
        ):
            if first_token_at is None:
                first_token_at = utcnow()
            usage = event_usage or usage
            yield event
    except asyncio.CancelledError as exc:
        outcome = RequestOutcome.CLIENT_CANCELLED
        error = exc
        raise
    except Exception as exc:
        outcome = RequestOutcome.ADAPTER_FAILURE
        error = exc
        yield f"event: error\ndata: {str(exc)}\n\n"
    finally:
        await _mark_runtime_connection_closed(redis, metrics_member)
        with suppress(Exception):
            await release_concurrency_slot(redis, concurrency_key)
        await enqueue_fact(
            {
                "request_id": request_id,
                "started_at": started_at,
                "ended_at": utcnow(),
                "endpoint_family": EndpointFamily.OPENAI_RESPONSES,
                "subject_id": auth.subject.id,
                "subject_type": auth.subject.type,
                "project_id": auth.project.id,
                "model_alias": route.model_alias.alias,
                "upstream_target_id": route.upstream.id,
                "streaming": True,
                "outcome": outcome,
                "usage": usage,
                "first_token_at": first_token_at,
                "error_class": type(error).__name__ if error else None,
                "error_detail": str(error) if error else None,
            },
            endpoint="stream_responses",
        )


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    redis: Redis = Depends(redis_dep),
    settings: Settings = Depends(settings_dep),
    client_ip: str = Depends(client_ip_dep),
):
    body = await request.json()
    streaming = bool(body.get("stream"))
    started_at = utcnow()
    request_id = request.headers.get("x-request-id") or str(uuid4())
    auth, route, rate_policy = await _resolve_proxy_context(
        request=request,
        redis=redis,
        settings=settings,
        body=body,
        client_ip=client_ip,
        request_id=request_id,
        started_at=started_at,
        endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
    )

    if streaming:
        concurrency_key = await _acquire_streaming_concurrency(
            redis=redis,
            auth=auth,
            route=route,
            rate_policy=rate_policy,
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
        )
        return StreamingResponse(
            _stream_anthropic_response(
                redis=redis,
                auth=auth,
                route=route,
                concurrency_key=concurrency_key,
                body=body,
                started_at=started_at,
                request_id=request_id,
            ),
            media_type="text/event-stream",
        )

    try:
        async with concurrency_slot(
            redis, key_id=auth.key.id, limit=rate_policy.concurrency_limit
        ):
            metrics_member = await _mark_runtime_connection_open(
                redis=redis, request_id=request_id, route=route
            )
            try:
                result = await anthropic_messages_once(
                    model_alias=route.model_alias, upstream=route.upstream, body=body
                )
            finally:
                await _mark_runtime_connection_closed(redis, metrics_member)
        await enqueue_fact(
            {
                "request_id": request_id,
                "started_at": started_at,
                "ended_at": utcnow(),
                "endpoint_family": EndpointFamily.ANTHROPIC_MESSAGES,
                "subject_id": auth.subject.id,
                "subject_type": auth.subject.type,
                "project_id": auth.project.id,
                "model_alias": route.model_alias.alias,
                "upstream_target_id": route.upstream.id,
                "streaming": False,
                "outcome": RequestOutcome.SUCCESS,
                "usage": result.usage,
            },
            endpoint="anthropic_messages",
        )
        return JSONResponse(jsonable_encoder(_plain(result.response)))
    except RateLimitExceeded as exc:
        await _raise_rate_limited_after_route(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
            auth=auth,
            streaming=False,
            route=route,
            exc=exc,
        )
    except Exception as exc:
        await _record_failure(
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
    redis: Redis,
    auth: AuthContext,
    route,
    concurrency_key: str,
    body: dict[str, Any],
    started_at: datetime,
    request_id: str,
):
    usage = None
    first_token_at: datetime | None = None
    outcome = RequestOutcome.SUCCESS
    error: BaseException | None = None
    metrics_member = await _mark_runtime_connection_open(
        redis=redis, request_id=request_id, route=route
    )
    try:
        async for event, event_usage in anthropic_messages_stream(
            model_alias=route.model_alias,
            upstream=route.upstream,
            body=body,
        ):
            if first_token_at is None:
                first_token_at = utcnow()
            usage = event_usage or usage
            yield event
    except asyncio.CancelledError as exc:
        outcome = RequestOutcome.CLIENT_CANCELLED
        error = exc
        raise
    except Exception as exc:
        outcome = RequestOutcome.ADAPTER_FAILURE
        error = exc
        yield f"event: error\ndata: {str(exc)}\n\n"
    finally:
        await _mark_runtime_connection_closed(redis, metrics_member)
        with suppress(Exception):
            await release_concurrency_slot(redis, concurrency_key)
        await enqueue_fact(
            {
                "request_id": request_id,
                "started_at": started_at,
                "ended_at": utcnow(),
                "endpoint_family": EndpointFamily.ANTHROPIC_MESSAGES,
                "subject_id": auth.subject.id,
                "subject_type": auth.subject.type,
                "project_id": auth.project.id,
                "model_alias": route.model_alias.alias,
                "upstream_target_id": route.upstream.id,
                "streaming": True,
                "outcome": outcome,
                "usage": usage,
                "first_token_at": first_token_at,
                "error_class": type(error).__name__ if error else None,
                "error_detail": str(error) if error else None,
            },
            endpoint="stream_anthropic",
        )


@router.get("/v1/models")
async def list_models(
    request: Request,
):
    raw_key = bearer_token(request)
    async with AsyncSessionLocal() as session:
        auth = await authenticate_gateway_key(session, raw_key)
        if not auth:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_gateway_key"
            )

        rows = await list_accessible_model_aliases(session, auth=auth)
        await session.rollback()

    now = int(utcnow().timestamp())
    return {
        "object": "list",
        "data": [
            {"id": alias, "object": "model", "created": now, "owned_by": "gateway"}
            for alias in rows
        ],
    }


async def _acquire_streaming_concurrency(
    *,
    redis: Redis,
    auth: AuthContext,
    route,
    rate_policy,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
) -> str:
    try:
        return await acquire_concurrency_slot(
            redis,
            key_id=auth.key.id,
            limit=rate_policy.concurrency_limit,
        )
    except RateLimitExceeded as exc:
        await _raise_rate_limited_after_route(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=endpoint_family,
            auth=auth,
            streaming=True,
            route=route,
            exc=exc,
        )


async def _raise_rate_limited_after_route(
    *,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
    auth: AuthContext,
    streaming: bool,
    route,
    exc: RateLimitExceeded,
) -> NoReturn:
    await _record_failure(
        request_id=request_id,
        started_at=started_at,
        endpoint_family=endpoint_family,
        auth=auth,
        model_alias=route.model_alias.alias,
        upstream_target_id=route.upstream.id,
        streaming=streaming,
        outcome=RequestOutcome.RATE_LIMITED,
        exc=exc,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
    ) from exc


async def _record_failure(
    *,
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
    await enqueue_fact(
        {
            "request_id": request_id,
            "started_at": started_at,
            "ended_at": utcnow(),
            "endpoint_family": endpoint_family,
            "subject_id": auth.subject.id,
            "subject_type": auth.subject.type,
            "project_id": auth.project.id,
            "model_alias": model_alias,
            "upstream_target_id": upstream_target_id,
            "streaming": streaming,
            "outcome": outcome,
            "usage": None,
            "error_class": type(exc).__name__,
            "error_detail": str(exc),
        },
        endpoint=endpoint_family.value,
    )


async def _mark_runtime_connection_open(
    *, redis: Redis, request_id: str, route
) -> str | None:
    with suppress(Exception):
        return await mark_connection_open(
            redis,
            request_id=request_id,
            info=route_info(route.model_alias, route.upstream),
        )
    return None


async def _mark_runtime_connection_closed(
    redis: Redis, metrics_member: str | None
) -> None:
    if not metrics_member:
        return
    with suppress(Exception):
        await mark_connection_closed(redis, metrics_member)


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


async def _authenticate_proxy_request(
    request: Request, session: AsyncSession
) -> AuthContext:
    raw_key = bearer_token(request)
    context = await authenticate_gateway_key(session, raw_key)
    if not context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_gateway_key"
        )
    return context


async def _record_unauthenticated_request(
    *,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
    model_alias: str | None,
    exc: HTTPException,
) -> None:
    await enqueue_fact(
        {
            "request_id": request_id,
            "started_at": started_at,
            "ended_at": utcnow(),
            "endpoint_family": endpoint_family,
            "subject_id": None,
            "subject_type": None,
            "project_id": None,
            "model_alias": model_alias,
            "upstream_target_id": None,
            "streaming": False,
            "outcome": RequestOutcome.AUTH_FAILURE,
            "usage": None,
            "error_class": str(exc.status_code),
            "error_detail": str(exc.detail),
        },
        endpoint=endpoint_family.value,
    )


async def _record_rejected_request(
    *,
    request_id: str,
    started_at: datetime,
    endpoint_family: EndpointFamily,
    auth: AuthContext,
    model_alias: str | None,
    outcome: RequestOutcome,
    exc: HTTPException,
) -> None:
    await enqueue_fact(
        {
            "request_id": request_id,
            "started_at": started_at,
            "ended_at": utcnow(),
            "endpoint_family": endpoint_family,
            "subject_id": auth.subject.id,
            "subject_type": auth.subject.type,
            "project_id": auth.project.id,
            "model_alias": model_alias,
            "upstream_target_id": None,
            "streaming": False,
            "outcome": outcome,
            "usage": None,
            "error_class": str(exc.status_code),
            "error_detail": str(exc.detail),
        },
        endpoint=endpoint_family.value,
    )
