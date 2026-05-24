from collections.abc import AsyncGenerator

from fastapi import Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.core.config import Settings, get_settings
from llm_gateway.db.session import get_session
from llm_gateway.services.rate_limit import redis_client
from llm_gateway.services.security import AuthContext, authenticate_gateway_key


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
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_gateway_key")


async def auth_dep(
    request: Request,
    session: AsyncSession = Depends(session_dep),
) -> AuthContext:
    raw_key = bearer_token(request)
    context = await authenticate_gateway_key(session, raw_key)
    if not context:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_gateway_key")
    return context


async def admin_dep(
    x_admin_token: str | None = Header(default=None),
    settings: Settings = Depends(settings_dep),
) -> None:
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_admin_token")

