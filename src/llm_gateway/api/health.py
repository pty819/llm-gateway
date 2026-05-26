import importlib.metadata
import inspect

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.api.deps import admin_dep, redis_dep, session_dep, settings_dep
from llm_gateway.core.config import Settings


router = APIRouter()


@router.get("/health/live")
async def live():
    return {"ok": True}


@router.get("/health/ready")
async def ready(
    session: AsyncSession = Depends(session_dep),
    redis: Redis = Depends(redis_dep),
):
    checks = {"postgres": False, "redis": False}
    try:
        await session.execute(text("select 1"))
        checks["postgres"] = True
    except Exception:
        checks["postgres"] = False
    try:
        pong_result = redis.ping()
        pong = await pong_result if inspect.isawaitable(pong_result) else pong_result
        checks["redis"] = bool(pong)
    except Exception:
        checks["redis"] = False
    return {"ok": all(checks.values()), "checks": checks}


@router.get("/admin/diagnostics", dependencies=[Depends(admin_dep)])
async def diagnostics(settings: Settings = Depends(settings_dep)):
    try:
        litellm_version = importlib.metadata.version("litellm")
    except Exception:
        litellm_version = "unknown"
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "litellm_version": litellm_version,
    }
