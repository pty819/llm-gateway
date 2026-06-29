from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.deps import session_dep
from llm_gateway.db.models import AuditEvent
from llm_gateway.services import analytics


router = APIRouter()


AnalyticsBucket = Literal["minute", "hour", "day"]
AnalyticsDimension = Literal[
    "model", "subject", "project", "endpoint", "outcome", "streaming"
]


@router.get("/usage/summary")
async def usage_summary(
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    subject_id: UUID | None = None,
    project_id: UUID | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
    session: AsyncSession = Depends(session_dep),
):
    return await analytics.usage_summary(
        session,
        start=start,
        end=end,
        model=model,
        subject_id=subject_id,
        project_id=project_id,
        limit=limit,
    )


@router.get("/usage/totals")
async def usage_totals(
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    subject_id: UUID | None = None,
    project_id: UUID | None = None,
    session: AsyncSession = Depends(session_dep),
):
    return await analytics.usage_totals(
        session,
        start=start,
        end=end,
        model=model,
        subject_id=subject_id,
        project_id=project_id,
    )


@router.get("/usage/ranking")
async def usage_ranking(
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(session_dep),
):
    return await analytics.usage_ranking(
        session,
        start=start,
        end=end,
        model=model,
        limit=limit,
    )


@router.get("/analytics/time-buckets")
async def analytics_time_buckets(
    start: datetime | None = None,
    end: datetime | None = None,
    bucket: AnalyticsBucket = "hour",
    model: str | None = None,
    subject_id: UUID | None = None,
    project_id: UUID | None = None,
    session: AsyncSession = Depends(session_dep),
):
    return await analytics.time_buckets(
        session,
        bucket=bucket,
        start=start,
        end=end,
        model=model,
        subject_id=subject_id,
        project_id=project_id,
    )


@router.get("/analytics/drilldown")
async def analytics_drilldown(
    dimension: AnalyticsDimension = "model",
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    subject_id: UUID | None = None,
    project_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(session_dep),
):
    return await analytics.drilldown(
        session,
        dimension=dimension,
        start=start,
        end=end,
        model=model,
        subject_id=subject_id,
        project_id=project_id,
        limit=limit,
    )


@router.get("/audit-events")
async def list_audit_events(session: AsyncSession = Depends(session_dep)):
    result = await session.execute(
        select(AuditEvent).order_by(col(AuditEvent.created_at).desc()).limit(200)
    )
    return result.scalars().all()
