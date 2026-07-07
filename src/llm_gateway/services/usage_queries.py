"""Usage aggregation queries (SQLAlchemy / Postgres).

These are heavy usage-SQL helpers that belong in the service layer (per
refactor-architecture-plan.md: "Heavy usage queries stay behind the analytics
service"). They were originally inlined in ``api/auth.py``; moved here so the
route modules stay thin and the aggregation logic is reusable/testable.

Public API (names dropped their ``_`` prefix when promoted to a service module):
- ``usage_summary_from_postgres`` — single aggregate row for a window + scope.
- ``usage_ranking_from_postgres`` — per-subject ranking, sorted by tokens desc.
- ``empty_usage_summary`` — the zero-row sentinel shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.db.models import RequestFact, RequestOutcome, Subject


async def usage_summary_from_postgres(
    session: AsyncSession,
    *,
    start: datetime | None,
    end: datetime | None,
    project_ids: list[UUID] | None = None,
    subject_ids: list[UUID] | None = None,
) -> dict[str, int]:
    if project_ids is not None and not project_ids:
        return empty_usage_summary()
    if subject_ids is not None and not subject_ids:
        return empty_usage_summary()

    total_tokens_expr = func.coalesce(
        RequestFact.total_tokens,
        func.coalesce(RequestFact.prompt_tokens, 0)
        + func.coalesce(RequestFact.completion_tokens, 0),
        0,
    )
    stmt = select(
        func.count(col(RequestFact.id)),
        func.coalesce(func.sum(RequestFact.prompt_tokens), 0),
        func.coalesce(func.sum(RequestFact.completion_tokens), 0),
        func.coalesce(func.sum(total_tokens_expr), 0),
        func.coalesce(
            func.sum(case((col(RequestFact.outcome) == RequestOutcome.SUCCESS, 1), else_=0)),
            0,
        ),
        func.coalesce(
            func.sum(case((col(RequestFact.outcome) != RequestOutcome.SUCCESS, 1), else_=0)),
            0,
        ),
    )
    if start is not None:
        stmt = stmt.where(col(RequestFact.started_at) >= start)
    if end is not None:
        stmt = stmt.where(col(RequestFact.started_at) < end)
    if project_ids is not None:
        stmt = stmt.where(col(RequestFact.project_id).in_(project_ids))
    if subject_ids is not None:
        stmt = stmt.where(col(RequestFact.subject_id).in_(subject_ids))

    row = (await session.execute(stmt)).one()
    return {
        "request_count": int(row[0] or 0),
        "prompt_tokens": int(row[1] or 0),
        "completion_tokens": int(row[2] or 0),
        "total_tokens": int(row[3] or 0),
        "success_count": int(row[4] or 0),
        "failure_count": int(row[5] or 0),
    }


def empty_usage_summary() -> dict[str, int]:
    return {
        "request_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "success_count": 0,
        "failure_count": 0,
    }


async def usage_ranking_from_postgres(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    project_ids: list[UUID] | None = None,
    subject_ids: list[UUID] | None = None,
    model: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Per-subject usage ranking, sorted by total_tokens desc.

    Mirrors ``usage_summary_from_postgres`` (same total_tokens coalesce expression,
    same Postgres aggregation) but groups by subject and orders by usage. Used by
    the manager-facing ranking endpoint; the manager permission check happens in
    the route handler before this runs. subject_id IS NULL rows are excluded,
    matching the admin ranking behavior.

    Scope is selected by passing exactly one of ``project_ids`` (filter on
    RequestFact.project_id) or ``subject_ids`` (filter on
    RequestFact.subject_id, used for team scope where membership is derived via
    TeamMembership). Empty lists short-circuit to [] like the summary builder.
    """
    if project_ids is not None and not project_ids:
        return []
    if subject_ids is not None and not subject_ids:
        return []

    total_tokens_expr = func.coalesce(
        RequestFact.total_tokens,
        func.coalesce(RequestFact.prompt_tokens, 0)
        + func.coalesce(RequestFact.completion_tokens, 0),
        0,
    )
    stmt = (
        select(
            Subject.id.label("subject_id"),
            Subject.name.label("subject_name"),
            Subject.login_username.label("login_username"),
            func.count(col(RequestFact.id)).label("request_count"),
            func.coalesce(func.sum(RequestFact.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(RequestFact.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(total_tokens_expr), 0).label("total_tokens"),
            func.coalesce(
                func.sum(case((col(RequestFact.outcome) == RequestOutcome.SUCCESS, 1), else_=0)),
                0,
            ).label("success_count"),
            func.coalesce(
                func.sum(case((col(RequestFact.outcome) != RequestOutcome.SUCCESS, 1), else_=0)),
                0,
            ).label("failure_count"),
        )
        .select_from(RequestFact)
        .outerjoin(Subject, RequestFact.subject_id == Subject.id)
        .where(col(RequestFact.subject_id).isnot(None))
    )
    if project_ids is not None:
        stmt = stmt.where(col(RequestFact.project_id).in_(project_ids))
    if subject_ids is not None:
        stmt = stmt.where(col(RequestFact.subject_id).in_(subject_ids))
    # Conditionally apply time bounds so a half-specified window (only start or
    # only end) behaves like usage_summary_from_postgres rather than silently
    # returning [] because `started_at < NULL` is always false.
    if start is not None:
        stmt = stmt.where(col(RequestFact.started_at) >= start)
    if end is not None:
        stmt = stmt.where(col(RequestFact.started_at) < end)
    if model is not None:
        stmt = stmt.where(col(RequestFact.model_alias) == model)
    stmt = (
        stmt.group_by(Subject.id, Subject.name, Subject.login_username)
        .order_by(desc(text("total_tokens")), desc(text("request_count")))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "subject_id": str(row.subject_id),
            "subject_name": row.subject_name or "无用户",
            "login_username": row.login_username,
            "request_count": int(row.request_count),
            "prompt_tokens": int(row.prompt_tokens),
            "completion_tokens": int(row.completion_tokens),
            "total_tokens": int(row.total_tokens),
            "success_count": int(row.success_count),
            "failure_count": int(row.failure_count),
        }
        for row in rows
    ]
