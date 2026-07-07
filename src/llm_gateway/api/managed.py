"""Managed-resource routes: subjects / roles / projects / teams / memberships /
usage (summary + ranking).

Sub-router has NO prefix — it is included by ``api/auth.py`` under the shared
``/auth`` prefix, so paths compose to ``/auth/managed/...`` unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api._auth_helpers import public_subject
from llm_gateway.api.deps import session_dep, user_session_dep
from llm_gateway.db.models import (
    Project,
    ProjectMembership,
    ResourceState,
    Subject,
    Team,
    TeamMembership,
    utcnow,
)
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.managed_memberships import (
    ManagedRole,
    managed_role_options,
    project_membership_payload,
    team_membership_payload,
)
from llm_gateway.services.security import UserSessionContext
from llm_gateway.services.usage_queries import (
    usage_ranking_from_postgres,
    usage_summary_from_postgres,
)

router = APIRouter()


class ManagedMembershipCreate(BaseModel):
    resource_id: UUID
    subject_id: UUID
    role: ManagedRole = ManagedRole.MEMBER


class ManagedTeamMembershipPatch(BaseModel):
    state: ResourceState


async def managed_projects_payload(session: AsyncSession, subject_id: UUID) -> list[dict[str, Any]]:
    stmt = (
        select(Project, ProjectMembership)
        .join(ProjectMembership, col(ProjectMembership.project_id) == col(Project.id))
        .where(
            col(Project.state) == ResourceState.ACTIVE,
            col(ProjectMembership.subject_id) == subject_id,
            col(ProjectMembership.role) == "manager",
        )
        .order_by(col(Project.name))
    )
    rows = (await session.execute(stmt)).all()
    return [{"project": project, "membership": membership} for project, membership in rows]


async def managed_teams_payload(session: AsyncSession, subject_id: UUID) -> list[dict[str, Any]]:
    stmt = (
        select(Team, TeamMembership)
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == subject_id,
            col(TeamMembership.role) == "manager",
        )
        .order_by(col(Team.name))
    )
    rows = (await session.execute(stmt)).all()
    return [{"team": team, "membership": membership} for team, membership in rows]


async def managed_project_ids(session: AsyncSession, subject_id: UUID) -> list[UUID]:
    rows = await managed_projects_payload(session, subject_id)
    return [row["project"].id for row in rows]


async def managed_team_ids(session: AsyncSession, subject_id: UUID) -> list[UUID]:
    rows = await managed_teams_payload(session, subject_id)
    return [row["team"].id for row in rows]


async def require_any_managed_resource(session: AsyncSession, subject_id: UUID) -> None:
    if await managed_project_ids(session, subject_id):
        return
    if await managed_team_ids(session, subject_id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="not_resource_manager",
    )


async def require_project_manager(
    session: AsyncSession, subject_id: UUID, project_id: UUID
) -> None:
    project_ids = await managed_project_ids(session, subject_id)
    if project_id not in project_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not_project_manager",
        )


async def require_team_manager(session: AsyncSession, subject_id: UUID, team_id: UUID) -> None:
    team_ids = await managed_team_ids(session, subject_id)
    if team_id not in team_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not_team_manager",
        )


async def get_active_subject(session: AsyncSession, subject_id: UUID) -> Subject:
    subject = await session.get(Subject, subject_id)
    if not subject or subject.state != ResourceState.ACTIVE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return subject


async def team_subject_ids(session: AsyncSession, team_ids: list[UUID]) -> list[UUID]:
    if not team_ids:
        return []
    result = await session.execute(
        select(col(TeamMembership.subject_id)).where(
            col(TeamMembership.team_id).in_(team_ids),
            col(TeamMembership.state) == ResourceState.ACTIVE,
        )
    )
    return list(result.scalars().all())


@router.get("/managed/subjects")
async def list_managed_candidate_subjects(
    q: str | None = None,
    limit: int = 20,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await require_any_managed_resource(session, context.subject.id)
    stmt = select(Subject).where(col(Subject.state) == ResourceState.ACTIVE)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            col(Subject.name).ilike(needle) | col(Subject.login_username).ilike(needle)
        )
    rows = (
        (await session.execute(stmt.order_by(col(Subject.name)).limit(max(1, min(limit, 50)))))
        .scalars()
        .all()
    )
    return [public_subject(subject) for subject in rows]


@router.get("/managed/roles")
async def list_managed_roles(
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await require_any_managed_resource(session, context.subject.id)
    return managed_role_options()


@router.get("/managed/projects")
async def list_managed_projects(
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    return await managed_projects_payload(session, context.subject.id)


@router.get("/managed/teams")
async def list_managed_teams(
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    return await managed_teams_payload(session, context.subject.id)


@router.get("/managed/project-memberships")
async def list_managed_project_memberships(
    resource_id: UUID,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await require_project_manager(session, context.subject.id, resource_id)
    rows = (
        await session.execute(
            select(ProjectMembership, Subject)
            .join(Subject, col(Subject.id) == col(ProjectMembership.subject_id))
            .where(col(ProjectMembership.project_id) == resource_id)
            .order_by(col(ProjectMembership.created_at).desc())
        )
    ).all()
    return [project_membership_payload(membership, subject) for membership, subject in rows]


@router.get("/managed/team-memberships")
async def list_managed_team_memberships(
    resource_id: UUID,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await require_team_manager(session, context.subject.id, resource_id)
    rows = (
        await session.execute(
            select(TeamMembership, Subject)
            .join(Subject, col(Subject.id) == col(TeamMembership.subject_id))
            .where(col(TeamMembership.team_id) == resource_id)
            .order_by(col(TeamMembership.created_at).desc())
        )
    ).all()
    return [team_membership_payload(membership, subject) for membership, subject in rows]


@router.get("/managed/usage/summary")
async def managed_usage_summary(
    scope: str,
    resource_id: UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    if start and end and (end - start).days > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="time_window_exceeds_90_days",
        )
    if start is None and end is None:
        end = utcnow()
        start = end - timedelta(days=30)

    if scope == "project":
        project_ids = await managed_project_ids(session, context.subject.id)
        if resource_id is not None:
            if resource_id not in project_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="not_project_manager",
                )
            project_ids = [resource_id]
        row = await usage_summary_from_postgres(
            session,
            start=start,
            end=end,
            project_ids=project_ids,
        )
    elif scope == "team":
        team_ids = await managed_team_ids(session, context.subject.id)
        if resource_id is not None:
            if resource_id not in team_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="not_team_manager",
                )
            team_ids = [resource_id]
        subject_ids = await team_subject_ids(session, team_ids)
        row = await usage_summary_from_postgres(
            session,
            start=start,
            end=end,
            subject_ids=subject_ids,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_managed_usage_scope",
        )
    return {
        "start": start,
        "end": end,
        "scope": scope,
        "resource_id": resource_id,
        **row,
    }


@router.get("/managed/usage/ranking")
async def managed_usage_ranking(
    scope: str,
    resource_id: UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    if start and end and (end - start).days > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="time_window_exceeds_90_days",
        )
    if start is None and end is None:
        end = utcnow()
        start = end - timedelta(days=30)

    # 权限校验在 Python 端先做，传入 SQL 时已是安全值，不依赖 SQL 层过滤正确性。
    # team 排行只含当前 ACTIVE 成员（team_subject_ids 已过滤
    # TeamMembership.state == ACTIVE），与 managed_usage_summary 的 team 分支一致。
    if scope == "project":
        await require_project_manager(session, context.subject.id, resource_id)
        ranking = await usage_ranking_from_postgres(
            session,
            start=start,
            end=end,
            project_ids=[resource_id],
            model=model,
            limit=limit,
        )
    elif scope == "team":
        await require_team_manager(session, context.subject.id, resource_id)
        subject_ids = await team_subject_ids(session, [resource_id])
        ranking = await usage_ranking_from_postgres(
            session,
            start=start,
            end=end,
            subject_ids=subject_ids,
            model=model,
            limit=limit,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_managed_usage_scope",
        )
    return {
        "start": start,
        "end": end,
        "scope": scope,
        "resource_id": resource_id,
        "ranking": ranking,
    }


@router.post("/managed/project-memberships")
async def add_managed_project_member(
    payload: ManagedMembershipCreate,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await require_project_manager(session, context.subject.id, payload.resource_id)
    subject = await get_active_subject(session, payload.subject_id)
    result = await session.execute(
        select(ProjectMembership).where(
            col(ProjectMembership.project_id) == payload.resource_id,
            col(ProjectMembership.subject_id) == payload.subject_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        membership.role = payload.role.value
        membership.updated_at = utcnow()
    else:
        membership = ProjectMembership(
            project_id=payload.resource_id,
            subject_id=payload.subject_id,
            role=payload.role.value,
        )
        session.add(membership)
        await session.flush()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="managed.project_membership.upsert",
        resource_type="project_membership",
        resource_id=membership.id,
        outcome="success",
        detail={
            "project_id": str(payload.resource_id),
            "subject_id": str(payload.subject_id),
        },
    )
    await session.commit()
    await session.refresh(membership)
    return project_membership_payload(membership, subject)


@router.delete("/managed/project-memberships/{membership_id}")
async def remove_managed_project_member(
    membership_id: UUID,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    membership = await session.get(ProjectMembership, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await require_project_manager(session, context.subject.id, membership.project_id)
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="managed.project_membership.delete",
        resource_type="project_membership",
        resource_id=membership.id,
        outcome="success",
        detail={
            "project_id": str(membership.project_id),
            "subject_id": str(membership.subject_id),
        },
    )
    await session.delete(membership)
    await session.commit()
    return {"ok": True}


@router.post("/managed/team-memberships")
async def add_managed_team_member(
    payload: ManagedMembershipCreate,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    await require_team_manager(session, context.subject.id, payload.resource_id)
    subject = await get_active_subject(session, payload.subject_id)
    result = await session.execute(
        select(TeamMembership).where(
            col(TeamMembership.team_id) == payload.resource_id,
            col(TeamMembership.subject_id) == payload.subject_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        membership.role = payload.role.value
        membership.state = ResourceState.ACTIVE
        membership.updated_at = utcnow()
    else:
        membership = TeamMembership(
            team_id=payload.resource_id,
            subject_id=payload.subject_id,
            role=payload.role.value,
        )
        session.add(membership)
        await session.flush()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="managed.team_membership.upsert",
        resource_type="team_membership",
        resource_id=membership.id,
        outcome="success",
        detail={
            "team_id": str(payload.resource_id),
            "subject_id": str(payload.subject_id),
        },
    )
    await session.commit()
    await session.refresh(membership)
    return team_membership_payload(membership, subject)


@router.patch("/managed/team-memberships/{membership_id}")
async def set_managed_team_member_state(
    membership_id: UUID,
    payload: ManagedTeamMembershipPatch,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    membership = await session.get(TeamMembership, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await require_team_manager(session, context.subject.id, membership.team_id)
    membership.state = payload.state
    membership.updated_at = utcnow()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="managed.team_membership.set_state",
        resource_type="team_membership",
        resource_id=membership.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    return membership
