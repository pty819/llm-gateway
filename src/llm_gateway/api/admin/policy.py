from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.admin._common import (
    _audit_update,
    _count_rows,
    _get_or_404,
)
from llm_gateway.api.deps import session_dep
from llm_gateway.db.models import (
    GatewayKey,
    Project,
    RatePolicy,
    ResourceState,
    Subject,
)
from llm_gateway.services.resource_payloads import apply_model_patch, paginated
from llm_gateway.services.facts import record_audit_event


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
async def list_rate_policies(
    scope: str | None = Query(default=None, max_length=20),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(session_dep),
):
    stmt = select(RatePolicy)
    if scope:
        stmt = stmt.where(col(RatePolicy.scope) == scope)
    total = await _count_rows(
        session, select(func.count()).select_from(stmt.subquery())
    )
    rows = (
        (
            await session.execute(
                stmt.order_by(col(RatePolicy.created_at).desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    names = await _resolve_scope_names(session, rows)
    items = []
    for policy in rows:
        item = policy.model_dump()
        item["scope_name"] = names.get(policy.scope_id)
        items.append(item)
    return paginated(items, total, limit, offset)


async def _resolve_scope_names(
    session: AsyncSession, policies: list[RatePolicy]
) -> dict[UUID, str | None]:
    """Batch-resolve scope display names for the current page only, grouped
    by scope so each page costs at most three small IN queries."""
    by_scope: dict[str, set[UUID]] = {}
    for policy in policies:
        by_scope.setdefault(policy.scope, set()).add(policy.scope_id)
    names: dict[UUID, str | None] = {}
    targets = {
        "subject": (Subject, Subject.name),
        "project": (Project, Project.name),
        "key": (GatewayKey, GatewayKey.name),
    }
    for scope, ids in by_scope.items():
        target = targets.get(scope)
        if target is None:
            continue
        model, name_col = target
        result = await session.execute(
            select(model.id, name_col).where(col(model.id).in_(ids))
        )
        for row in result.all():
            names[row.id] = row[1]
    return names


@router.patch("/rate-policies/{policy_id}")
async def update_rate_policy(
    policy_id: UUID,
    payload: RatePolicyUpdate,
    session: AsyncSession = Depends(session_dep),
):
    policy = await _get_or_404(session, RatePolicy, policy_id)
    apply_model_patch(policy, payload)
    await _audit_update(
        session, "rate_policy.update", "rate_policy", policy.id, payload
    )
    await session.commit()
    await session.refresh(policy)
    return policy
