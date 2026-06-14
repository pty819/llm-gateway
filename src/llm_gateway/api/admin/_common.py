from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.db.models import (
    Project,
    ProjectMembership,
    GatewayKey,
    ModelEntitlement,
    RatePolicy,
    RequestFact,
    ResourceState,
    Subject,
    UpstreamTarget,
)
from llm_gateway.services.security import (
    is_employee_username,
    normalize_username,
)


class StatePatch(BaseModel):
    state: ResourceState


async def _get_or_404(session: AsyncSession, model, item_id: UUID):
    item = await session.get(model, item_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{model.__name__}_not_found"
        )
    return item


def _validate_login_username(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_username(value)
    if not normalized:
        return None
    if not is_employee_username(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username_must_match_employee_id",
        )
    return normalized


async def _ensure_login_username_available(
    session: AsyncSession,
    login_username: str,
    *,
    subject_id: UUID | None = None,
) -> None:
    result = await session.execute(
        select(Subject).where(col(Subject.login_username) == login_username)
    )
    existing = result.scalar_one_or_none()
    if existing and existing.id != subject_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="username_already_registered"
        )


async def _count_rows(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _detach_upstream_usage(
    session: AsyncSession, upstream: UpstreamTarget
) -> int:
    request_count = await _count_rows(
        session,
        select(func.count(col(RequestFact.id))).where(
            col(RequestFact.upstream_target_id) == upstream.id
        ),
    )
    if not request_count:
        return 0
    await session.execute(
        update(RequestFact)
        .where(col(RequestFact.upstream_target_id) == upstream.id)
        .values(upstream_target_id=None)
    )
    return request_count


async def _validate_homogeneous_upstream_payload(
    session: AsyncSession,
    *,
    payload,
    existing: UpstreamTarget | None = None,
) -> None:
    from llm_gateway.api.admin.routing import UpstreamTargetCreate

    model_alias_id = (
        payload.model_alias_id
        if isinstance(payload, UpstreamTargetCreate)
        else existing.model_alias_id
        if existing
        else None
    )
    if model_alias_id is None:
        return

    incoming = payload.model_dump(exclude_unset=True)
    merged = {
        "api_key_ref": existing.api_key_ref if existing else None,
        "api_key_value": existing.api_key_value if existing else None,
        "health_path": existing.health_path if existing else "/models",
        "extra_headers": dict(existing.extra_headers or {}) if existing else {},
        "state": existing.state if existing else ResourceState.ACTIVE,
    }
    merged.update({key: incoming[key] for key in merged if key in incoming})
    if merged["state"] != ResourceState.ACTIVE:
        return

    result = await session.execute(
        select(UpstreamTarget).where(
            col(UpstreamTarget.model_alias_id) == model_alias_id,
            col(UpstreamTarget.state) == ResourceState.ACTIVE,
        )
    )
    siblings = [
        item
        for item in result.scalars().all()
        if existing is None or item.id != existing.id
    ]
    if not siblings:
        return

    sibling = siblings[0]
    expected = {
        "api_key_ref": sibling.api_key_ref,
        "api_key_value": sibling.api_key_value,
        "health_path": sibling.health_path,
        "extra_headers": dict(sibling.extra_headers or {}),
    }
    actual = {key: merged[key] for key in expected}
    if actual != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="upstream_replicas_must_share_key_headers_and_health_path",
        )


async def _delete_project_without_usage(
    session: AsyncSession, project: Project
) -> None:
    await session.execute(
        delete(ProjectMembership).where(col(ProjectMembership.project_id) == project.id)
    )
    await session.execute(
        delete(ModelEntitlement).where(col(ModelEntitlement.project_id) == project.id)
    )
    await session.execute(
        delete(RatePolicy).where(
            col(RatePolicy.scope) == "project", col(RatePolicy.scope_id) == project.id
        )
    )
    await session.execute(
        delete(GatewayKey).where(col(GatewayKey.project_id) == project.id)
    )
    await session.delete(project)


async def _audit_update(
    session: AsyncSession,
    action: str,
    resource_type: str,
    resource_id: UUID,
    payload: BaseModel,
) -> None:
    from llm_gateway.services.facts import record_audit_event

    await record_audit_event(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="success",
        detail=payload.model_dump(exclude_unset=True, mode="json"),
    )
