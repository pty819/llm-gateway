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
from llm_gateway.db.models import EndpointFamily, RequestOutcome, utcnow
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.policy import (
    PolicyDenied,
    list_accessible_model_aliases,
    resolve_route_context,
)
from llm_gateway.services.proxy_accounting import (
    outcome_for_http_status,
    record_proxy_error,
    record_proxy_fact,
    requested_model_alias,
)
from llm_gateway.services.rate_limit import (
    RateLimitExceeded,
    acquire_concurrency_slot,
    check_request_rate,
    concurrency_slot,
    release_concurrency_slot,
    resolve_effective_rate_policy,
)
from llm_gateway.services.runtime_metrics import tracked_runtime_connection
from llm_gateway.services.security import AuthContext, authenticate_gateway_key
from llm_gateway.services.streaming import HEARTBEAT_FRAME, iter_with_heartbeat
from llm_gateway.services.upstream_client import (
    upstream_request_once,
    upstream_request_stream,
)
from llm_gateway.services.upstream_routing import touch_sticky_route

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
            redis=redis,
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
            await record_proxy_fact(
                request_id=request_id,
                started_at=started_at,
                endpoint_family=endpoint_family,
                model_alias=requested_model_alias(body),
                streaming=False,
                outcome=RequestOutcome.AUTH_FAILURE,
                usage=None,
                error_class=str(exc.status_code),
                error_detail=str(exc.detail),
                endpoint=endpoint_family.value,
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
            await record_proxy_fact(
                request_id=request_id,
                started_at=started_at,
                endpoint_family=endpoint_family,
                auth=auth,
                model_alias=requested_model_alias(body),
                streaming=False,
                outcome=outcome_for_http_status(exc.status_code),
                usage=None,
                error_class=str(exc.status_code),
                error_detail=str(exc.detail),
                endpoint=endpoint_family.value,
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


async def _proxy_endpoint(
    *,
    endpoint_family: EndpointFamily,
    nonstream_endpoint: str,
    stream_endpoint: str,
    request: Request,
    redis: Redis,
    settings: Settings,
    client_ip: str,
):
    """Unified proxy handler for all three protocol families. Behavior is
    identical across families; only endpoint_family and the fact-recording
    endpoint labels differ, so the three route handlers are thin delegates."""
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
        endpoint_family=endpoint_family,
    )

    if streaming:
        return StreamingResponse(
            _stream_endpoint(
                endpoint_family=endpoint_family,
                stream_endpoint=stream_endpoint,
                redis=redis,
                auth=auth,
                route=route,
                rate_policy=rate_policy,
                body=body,
                started_at=started_at,
                request_id=request_id,
                keepalive_seconds=settings.stream_keepalive_seconds,
                request=request,
                watchdog_interval=settings.stream_disconnect_watchdog_seconds,
            ),
            media_type="text/event-stream",
        )

    try:
        async with concurrency_slot(
            redis,
            key_id=auth.key.id,
            limit=rate_policy.concurrency_limit,
        ):
            async with tracked_runtime_connection(redis, request_id=request_id, route=route):
                result = await upstream_request_once(
                    endpoint_family=endpoint_family,
                    model_alias=route.model_alias,
                    upstream=route.upstream,
                    body=body,
                )
        await record_proxy_fact(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=endpoint_family,
            auth=auth,
            route=route,
            streaming=False,
            outcome=RequestOutcome.SUCCESS,
            usage=result.usage,
            endpoint=nonstream_endpoint,
        )
        return JSONResponse(jsonable_encoder(_plain(result.response)))
    except RateLimitExceeded as exc:
        await _raise_rate_limited_after_route(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=endpoint_family,
            auth=auth,
            streaming=False,
            route=route,
            exc=exc,
        )
    except Exception as exc:
        await record_proxy_error(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=endpoint_family,
            auth=auth,
            route=route,
            model_alias=None,
            streaming=False,
            outcome=RequestOutcome.ADAPTER_FAILURE,
            exc=exc,
        )
        return _error_response(status.HTTP_502_BAD_GATEWAY, "adapter_failure", exc)
    finally:
        await _touch_route_sticky(redis, auth=auth, route=route)


async def _stream_endpoint(
    *,
    endpoint_family: EndpointFamily,
    stream_endpoint: str,
    redis: Redis,
    auth: AuthContext,
    route,
    rate_policy,
    body: dict[str, Any],
    started_at: datetime,
    request_id: str,
    keepalive_seconds: float,
    request: Request,
    watchdog_interval: float,
):
    # Lazy acquire: do this inside the generator so acquire and consume share
    # one coroutine — eliminates the "slot acquired but generator never
    # started" construction window that leaked slots on early disconnect.
    try:
        concurrency_key = await acquire_concurrency_slot(
            redis,
            key_id=auth.key.id,
            limit=rate_policy.concurrency_limit,
        )
    except RateLimitExceeded as exc:
        # The StreamingResponse has already begun (200 sent); we can't turn
        # this into a 429. Record the fact and emit an SSE error frame so the
        # client sees something instead of a silent hang.
        await record_proxy_fact(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=endpoint_family,
            auth=auth,
            route=route,
            streaming=True,
            outcome=RequestOutcome.RATE_LIMITED,
            usage=None,
            error_class="RateLimitExceeded",
            error_detail=str(exc),
            endpoint=stream_endpoint,
        )
        yield f"event: error\ndata: {str(exc)}\n\n"
        return

    usage = None
    first_token_at: datetime | None = None
    outcome = RequestOutcome.SUCCESS
    error: BaseException | None = None
    try:
        async with tracked_runtime_connection(redis, request_id=request_id, route=route):
            async for event, event_usage in iter_with_heartbeat(
                upstream_request_stream(
                    endpoint_family=endpoint_family,
                    model_alias=route.model_alias,
                    upstream=route.upstream,
                    body=body,
                ),
                interval_seconds=keepalive_seconds,
                disconnect_check=request.is_disconnected,
                disconnect_interval=watchdog_interval,
            ):
                if event == HEARTBEAT_FRAME:
                    yield event
                    continue
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
        with suppress(Exception):
            await release_concurrency_slot(redis, concurrency_key)
        await _touch_route_sticky(redis, auth=auth, route=route)
        await record_proxy_fact(
            request_id=request_id,
            started_at=started_at,
            endpoint_family=endpoint_family,
            auth=auth,
            route=route,
            streaming=True,
            outcome=outcome,
            usage=usage,
            first_token_at=first_token_at,
            error_class=type(error).__name__ if error else None,
            error_detail=str(error) if error else None,
            endpoint=stream_endpoint,
        )


@router.post("/v1/chat/completions")
async def openai_chat_completions(
    request: Request,
    redis: Redis = Depends(redis_dep),
    settings: Settings = Depends(settings_dep),
    client_ip: str = Depends(client_ip_dep),
):
    return await _proxy_endpoint(
        endpoint_family=EndpointFamily.OPENAI_CHAT,
        nonstream_endpoint="chat_completions",
        stream_endpoint="stream_openai",
        request=request,
        redis=redis,
        settings=settings,
        client_ip=client_ip,
    )


@router.post("/v1/responses")
async def openai_responses(
    request: Request,
    redis: Redis = Depends(redis_dep),
    settings: Settings = Depends(settings_dep),
    client_ip: str = Depends(client_ip_dep),
):
    return await _proxy_endpoint(
        endpoint_family=EndpointFamily.OPENAI_RESPONSES,
        nonstream_endpoint="responses",
        stream_endpoint="stream_responses",
        request=request,
        redis=redis,
        settings=settings,
        client_ip=client_ip,
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
    await record_proxy_error(
        request_id=request_id,
        started_at=started_at,
        endpoint_family=endpoint_family,
        auth=auth,
        route=route,
        model_alias=None,
        streaming=streaming,
        outcome=RequestOutcome.RATE_LIMITED,
        exc=exc,
    )
    raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc


async def _touch_route_sticky(redis: Redis, *, auth: AuthContext, route) -> None:
    with suppress(Exception):
        await touch_sticky_route(
            redis,
            key_id=auth.key.id,
            model_alias_id=route.model_alias.id,
            upstream_id=route.upstream.id,
            ttl_seconds=route.model_alias.sticky_ttl_seconds,
        )


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
