from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.admin._common import (
    _audit_update,
    _get_or_404,
)
from llm_gateway.api.deps import session_dep
from llm_gateway.db.models import RatePolicy, ResourceState
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.resource_payloads import apply_model_patch

router = APIRouter()


class RatePolicyCreate(BaseModel):
    scope: str
    scope_id: UUID
    requests_per_minute: int | None = None
    concurrency_limit: int | None = None


class RatePolicyUpdate(BaseModel):
    requests_per_minute: int | None = None
    concurrency_limit: int | None = None
    state: ResourceState | None = None


@router.post("/rate-policies")
async def create_rate_policy(
    payload: RatePolicyCreate, session: AsyncSession = Depends(session_dep)
):
    policy = RatePolicy(**payload.model_dump())
    session.add(policy)
    await session.flush()
    await record_audit_event(
        session,
        action="rate_policy.create",
        resource_type="rate_policy",
        resource_id=policy.id,
        outcome="success",
        detail={"scope": policy.scope, "scope_id": str(policy.scope_id)},
    )
    await session.commit()
    await session.refresh(policy)
    return policy


@router.get("/rate-policies")
async def list_rate_policies(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(select(RatePolicy).order_by(col(RatePolicy.created_at).desc()))
    return result.scalars().all()


@router.patch("/rate-policies/{policy_id}")
async def update_rate_policy(
    policy_id: UUID,
    payload: RatePolicyUpdate,
    session: AsyncSession = Depends(session_dep),
):
    policy = await _get_or_404(session, RatePolicy, policy_id)
    apply_model_patch(policy, payload)
    await _audit_update(session, "rate_policy.update", "rate_policy", policy.id, payload)
    await session.commit()
    await session.refresh(policy)
    return policy
