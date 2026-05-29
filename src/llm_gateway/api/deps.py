from collections.abc import AsyncGenerator

from fastapi import Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.core.config import Settings, get_settings
from llm_gateway.db.session import get_session
from llm_gateway.services.rate_limit import redis_client
from llm_gateway.services.security import (
    AuthContext,
    UserSessionContext,
    authenticate_gateway_key,
    authenticate_user_session,
    ensure_builtin_identity,
)


async def session_dep() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


def settings_dep() -> Settings:
    return get_settings()


def redis_dep() -> Redis:
    return redis_client


def client_ip_dep(request: Request, settings: Settings = Depends(settings_dep)) -> str:
    if settings.trusted_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    if request.client:
        return request.client.host
    return ""


def bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    anthropic_key = request.headers.get("x-api-key")
    if anthropic_key:
        return anthropic_key.strip()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_gateway_key"
    )


async def auth_dep(
    request: Request,
    session: AsyncSession = Depends(session_dep),
) -> AuthContext:
    raw_key = bearer_token(request)
    context = await authenticate_gateway_key(session, raw_key)
    if not context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_gateway_key"
        )
    return context


async def admin_dep(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
    session: AsyncSession = Depends(session_dep),
) -> None:
    if not x_admin_token or x_admin_token != settings.admin_token:
        raw_token = x_session_token or _session_token(request)
        if not raw_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_admin_token"
            )
        await ensure_builtin_identity(session, settings)
        await session.commit()
        context = await authenticate_user_session(session, raw_token)
        if not context or not context.subject.is_admin:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_admin_token"
            )


async def user_session_dep(
    request: Request,
    session: AsyncSession = Depends(session_dep),
) -> UserSessionContext:
    raw_token = _session_token(request)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_session_token"
        )
    context = await authenticate_user_session(session, raw_token)
    if not context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session_token"
        )
    return context


def _session_token(request: Request) -> str | None:
    explicit = request.headers.get("x-session-token")
    if explicit:
        return explicit.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer sess-"):
        return auth[7:].strip()
    return None
