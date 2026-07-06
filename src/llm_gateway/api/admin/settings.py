"""Admin endpoints for runtime-tunable system settings.

Today this covers only the health-check enable/disable toggle — a Redis-backed
runtime override that lets an admin stop the sidecar's automatic upstream
probing without restarting the process. The sidecar re-reads the override every
cycle, so a toggle takes effect within one interval (≤3s).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.api.deps import redis_dep, session_dep
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services import health_checker
from llm_gateway.services.facts import record_audit_event


router = APIRouter()


class HealthCheckConfig(BaseModel):
    enabled: bool
    source: str  # "redis_override" | "env_default"


class HealthCheckPatch(BaseModel):
    enabled: bool


@router.get("/health-check", response_model=HealthCheckConfig)
async def get_health_check_config(redis: Redis = Depends(redis_dep)):
    """Read the effective health-check enabled state + its source.

    The effective value is the Redis override if set, otherwise the env-var
    default. `source` tells the admin whether they're looking at an explicit
    override or the baked-in default.
    """
    enabled, source = await health_checker.effective_enabled(redis)
    return HealthCheckConfig(enabled=enabled, source=source)


@router.patch("/health-check", response_model=HealthCheckConfig)
async def patch_health_check_config(
    payload: HealthCheckPatch,
    redis: Redis = Depends(redis_dep),
):
    """Toggle the health-check at runtime via a Redis override.

    enabled=False writes a "0" sentinel to Redis → the sidecar skips probing
    next cycle. enabled=True deletes the override → falls back to the env-var
    default (which is usually on). This asymmetry is intentional: "enable"
    means "withdraw my emergency-stop override", not "force on regardless of
    env". If the env itself sets health_check_enabled=false, re-enabling here
    returns to that default (still off) — the admin sees `source: env_default`
    and knows they need to change the env var for a permanent enable.

    An audit row is recorded so the toggle is traceable.
    """
    await health_checker.set_enabled_override(redis, payload.enabled)

    # Best-effort audit: a Redis success must survive a PG failure since the
    # toggle already took effect in Redis.
    new_enabled, source = await health_checker.effective_enabled(redis)
    try:
        async with AsyncSessionLocal() as session:
            await record_audit_event(
                session,
                action="health_check.toggle",
                resource_type="system",
                resource_id=None,
                outcome="enabled" if new_enabled else "disabled",
                detail={"enabled": new_enabled, "source": source},
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — audit is best-effort
        pass

    return HealthCheckConfig(enabled=new_enabled, source=source)
