from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.core.config import Settings
from llm_gateway.db.models import Project, RequestFact, Subject


AnalyticsBucket = Literal["minute", "hour", "day"]
AnalyticsDimension = Literal[
    "model", "subject", "project", "endpoint", "outcome", "streaming"
]


class DuckDBAnalyticsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DuckDBRefreshResult:
    enabled: bool
    path: str
    rows_copied: int
    row_count: int
    min_started_at: datetime | None
    max_started_at: datetime | None
    file_size_bytes: int


@dataclass(frozen=True)
class DuckDBStatus:
    enabled: bool
    path: str
    exists: bool
    row_count: int
    min_started_at: datetime | None
    max_started_at: datetime | None
    file_size_bytes: int


class DuckDBAnalyticsStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = Path(settings.analytics_duckdb_path).expanduser()

    async def refresh(
        self,
        session: AsyncSession,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> DuckDBRefreshResult:
        self._require_enabled()
        copied = 0
        offset = 0
        remaining = limit
        while True:
            batch_limit = min(5_000, remaining) if remaining else 5_000
            rows = await self._load_rows(
                session,
                start=start,
                end=end,
                limit=batch_limit,
                offset=offset,
            )
            if not rows:
                break
            copied += await asyncio.to_thread(self._replace_rows, rows)
            offset += len(rows)
            if remaining is not None:
                remaining -= len(rows)
                if remaining <= 0:
                    break
        status = await self.status()
        return DuckDBRefreshResult(
            enabled=status.enabled,
            path=status.path,
            rows_copied=copied,
            row_count=status.row_count,
            min_started_at=status.min_started_at,
            max_started_at=status.max_started_at,
            file_size_bytes=status.file_size_bytes,
        )

    async def status(self) -> DuckDBStatus:
        enabled = self.settings.analytics_duckdb_enabled
        if not enabled:
            return DuckDBStatus(
                enabled=False,
                path=str(self.path),
                exists=self.path.exists(),
                row_count=0,
                min_started_at=None,
                max_started_at=None,
                file_size_bytes=self._file_size(),
            )
        return await asyncio.to_thread(self._status_sync)

    async def time_buckets(
        self,
        *,
        bucket: AnalyticsBucket,
        start: datetime | None = None,
        end: datetime | None = None,
        model: str | None = None,
        subject_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        self._require_enabled()
        sql, params = self._bucket_query(
            bucket=bucket,
            start=start,
            end=end,
            model=model,
            subject_id=subject_id,
            project_id=project_id,
        )
        return await asyncio.to_thread(self._query_rows, sql, params)

    async def drilldown(
        self,
        *,
        dimension: AnalyticsDimension,
        start: datetime | None = None,
        end: datetime | None = None,
        model: str | None = None,
        subject_id: UUID | None = None,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._require_enabled()
        dimension_expr, label_expr = _dimension_sql(dimension)
        where_sql, params = _where_sql(
            start=start,
            end=end,
            model=model,
            subject_id=subject_id,
            project_id=project_id,
        )
        params.append(limit)
        sql = f"""
            select
                {dimension_expr} as dimension_id,
                {label_expr} as dimension_label,
                {_metric_sql()}
            from request_facts
            {where_sql}
            group by 1, 2
            order by count(*) desc
            limit ?
        """
        return await asyncio.to_thread(self._query_rows, sql, params)

    async def usage_summary(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        self._require_enabled()
        where_sql, params = _where_sql(
            start=start, end=end, model=None, subject_id=None, project_id=None
        )
        sql = f"""
            select
                model_alias,
                subject_id,
                project_id,
                count(*)::BIGINT as request_count,
                coalesce(sum(prompt_tokens), 0)::BIGINT as prompt_tokens,
                coalesce(sum(completion_tokens), 0)::BIGINT as completion_tokens,
                coalesce(sum(
                    coalesce(
                        total_tokens,
                        coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0),
                        0
                    )
                ), 0)::BIGINT as total_tokens,
                coalesce(sum(case when outcome = 'success' then 1 else 0 end), 0)::BIGINT
                    as success_count,
                coalesce(sum(case when outcome <> 'success' then 1 else 0 end), 0)::BIGINT
                    as failure_count
            from request_facts
            {where_sql}
            group by model_alias, subject_id, project_id
        """
        return await asyncio.to_thread(self._query_rows, sql, params)

    async def usage_ranking(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        model: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self._require_enabled()
        where_sql, params = _where_sql(
            start=start, end=end, model=model, subject_id=None, project_id=None
        )
        clauses = [where_sql.removeprefix("where ").strip()] if where_sql else []
        clauses.append("subject_id is not null")
        params.append(limit)
        sql = f"""
            select
                subject_id,
                subject_login_username as login_username,
                coalesce(subject_name, subject_login_username, subject_id) as subject_name,
                count(*)::BIGINT as request_count,
                coalesce(sum(prompt_tokens), 0)::BIGINT as prompt_tokens,
                coalesce(sum(completion_tokens), 0)::BIGINT as completion_tokens,
                coalesce(sum(
                    coalesce(
                        total_tokens,
                        coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0),
                        0
                    )
                ), 0)::BIGINT as total_tokens
            from request_facts
            where {" and ".join(clauses)}
            group by subject_id, subject_login_username, subject_name
            order by total_tokens desc, request_count desc
            limit ?
        """
        return await asyncio.to_thread(self._query_rows, sql, params)

    async def _load_rows(
        self,
        session: AsyncSession,
        *,
        start: datetime | None,
        end: datetime | None,
        limit: int | None,
        offset: int,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(
                RequestFact,
                col(Subject.login_username).label("subject_login_username"),
                col(Subject.name).label("subject_name"),
                col(Project.name).label("project_name"),
            )
            .outerjoin(Subject, col(RequestFact.subject_id) == col(Subject.id))
            .outerjoin(Project, col(RequestFact.project_id) == col(Project.id))
            .order_by(col(RequestFact.started_at), col(RequestFact.request_id))
        )
        if start:
            stmt = stmt.where(col(RequestFact.started_at) >= start)
        if end:
            stmt = stmt.where(col(RequestFact.started_at) < end)
        if limit:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)

        result = await session.execute(stmt)
        rows = []
        for fact, subject_login_username, subject_name, project_name in result.all():
            rows.append(
                {
                    "id": str(fact.id),
                    "request_id": fact.request_id,
                    "started_at": fact.started_at,
                    "ended_at": fact.ended_at,
                    "endpoint_family": _value(fact.endpoint_family),
                    "subject_id": _optional_uuid(fact.subject_id),
                    "subject_type": _value(fact.subject_type),
                    "subject_login_username": subject_login_username,
                    "subject_name": subject_name,
                    "project_id": _optional_uuid(fact.project_id),
                    "project_name": project_name,
                    "model_alias": fact.model_alias,
                    "upstream_target_id": _optional_uuid(fact.upstream_target_id),
                    "streaming": fact.streaming,
                    "outcome": _value(fact.outcome),
                    "usage_source": _value(fact.usage_source),
                    "prompt_tokens": fact.prompt_tokens,
                    "completion_tokens": fact.completion_tokens,
                    "total_tokens": fact.total_tokens,
                    "cached_tokens": fact.cached_tokens,
                    "latency_ms": fact.latency_ms,
                    "time_to_first_token_ms": fact.time_to_first_token_ms,
                    "stream_duration_ms": fact.stream_duration_ms,
                    "retry_count": fact.retry_count,
                    "fallback_count": fact.fallback_count,
                    "fallback_tokens": fact.fallback_tokens,
                    "queue_ms": fact.queue_ms,
                    "prefill_ms": fact.prefill_ms,
                    "decode_ms": fact.decode_ms,
                    "kv_cache_usage": fact.kv_cache_usage,
                    "error_class": fact.error_class,
                }
            )
        return rows

    def _replace_rows(self, rows: list[dict[str, Any]]) -> int:
        duckdb = _duckdb()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.path)) as connection:
            _ensure_schema(connection)
            if not rows:
                return 0
            request_ids = [(row["request_id"],) for row in rows]
            connection.executemany(
                "delete from request_facts where request_id = ?", request_ids
            )
            connection.executemany(_insert_sql(), [_row_tuple(row) for row in rows])
            return len(rows)

    def _status_sync(self) -> DuckDBStatus:
        duckdb = _duckdb()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.path)) as connection:
            _ensure_schema(connection)
            row = connection.execute(
                """
                select
                    count(*)::BIGINT as row_count,
                    min(started_at) as min_started_at,
                    max(started_at) as max_started_at
                from request_facts
                """
            ).fetchone()
        return DuckDBStatus(
            enabled=True,
            path=str(self.path),
            exists=self.path.exists(),
            row_count=int(row[0] or 0),
            min_started_at=row[1],
            max_started_at=row[2],
            file_size_bytes=self._file_size(),
        )

    def _query_rows(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        duckdb = _duckdb()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.path), read_only=False) as connection:
            _ensure_schema(connection)
            result = connection.execute(sql, params)
            columns = [column[0] for column in result.description]
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]

    def _bucket_query(
        self,
        *,
        bucket: AnalyticsBucket,
        start: datetime | None,
        end: datetime | None,
        model: str | None,
        subject_id: UUID | None,
        project_id: UUID | None,
    ) -> tuple[str, list[Any]]:
        where_sql, params = _where_sql(
            start=start,
            end=end,
            model=model,
            subject_id=subject_id,
            project_id=project_id,
        )
        sql = f"""
            select
                date_trunc('{bucket}', started_at) as bucket_start,
                {_metric_sql()}
            from request_facts
            {where_sql}
            group by 1
            order by 1
        """
        return sql, params

    def _require_enabled(self) -> None:
        if not self.settings.analytics_duckdb_enabled:
            raise DuckDBAnalyticsUnavailable("duckdb_analytics_disabled")

    def _file_size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise DuckDBAnalyticsUnavailable("duckdb_dependency_missing") from exc
    return duckdb


def _ensure_schema(connection: Any) -> None:
    connection.execute(
        """
        create table if not exists request_facts (
            request_id varchar primary key,
            id varchar not null,
            started_at timestamp not null,
            ended_at timestamp,
            endpoint_family varchar not null,
            subject_id varchar,
            subject_type varchar,
            subject_login_username varchar,
            subject_name varchar,
            project_id varchar,
            project_name varchar,
            model_alias varchar,
            upstream_target_id varchar,
            streaming boolean not null,
            outcome varchar not null,
            usage_source varchar not null,
            prompt_tokens bigint,
            completion_tokens bigint,
            total_tokens bigint,
            cached_tokens bigint,
            latency_ms bigint,
            time_to_first_token_ms bigint,
            stream_duration_ms bigint,
            retry_count bigint not null,
            fallback_count bigint not null,
            fallback_tokens bigint,
            queue_ms bigint,
            prefill_ms bigint,
            decode_ms bigint,
            kv_cache_usage double,
            error_class varchar
        )
        """
    )


def _insert_sql() -> str:
    columns = ", ".join(_columns())
    placeholders = ", ".join(["?"] * len(_columns()))
    return f"insert into request_facts ({columns}) values ({placeholders})"


def _columns() -> list[str]:
    return [
        "request_id",
        "id",
        "started_at",
        "ended_at",
        "endpoint_family",
        "subject_id",
        "subject_type",
        "subject_login_username",
        "subject_name",
        "project_id",
        "project_name",
        "model_alias",
        "upstream_target_id",
        "streaming",
        "outcome",
        "usage_source",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "latency_ms",
        "time_to_first_token_ms",
        "stream_duration_ms",
        "retry_count",
        "fallback_count",
        "fallback_tokens",
        "queue_ms",
        "prefill_ms",
        "decode_ms",
        "kv_cache_usage",
        "error_class",
    ]


def _row_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[column] for column in _columns())


def _where_sql(
    *,
    start: datetime | None,
    end: datetime | None,
    model: str | None,
    subject_id: UUID | None,
    project_id: UUID | None,
) -> tuple[str, list[Any]]:
    clauses = []
    params: list[Any] = []
    if start:
        clauses.append("started_at >= ?")
        params.append(start)
    if end:
        clauses.append("started_at < ?")
        params.append(end)
    if model:
        clauses.append("model_alias = ?")
        params.append(model)
    if subject_id:
        clauses.append("subject_id = ?")
        params.append(str(subject_id))
    if project_id:
        clauses.append("project_id = ?")
        params.append(str(project_id))
    return (f"where {' and '.join(clauses)}" if clauses else ""), params


def _metric_sql() -> str:
    effective_total = (
        "coalesce(total_tokens, coalesce(prompt_tokens, 0) "
        "+ coalesce(completion_tokens, 0), 0)"
    )
    return f"""
        count(*)::BIGINT as request_count,
        coalesce(sum(prompt_tokens), 0)::BIGINT as prompt_tokens,
        coalesce(sum(completion_tokens), 0)::BIGINT as completion_tokens,
        coalesce(sum({effective_total}), 0)::BIGINT as total_tokens,
        coalesce(sum(cached_tokens), 0)::BIGINT as cached_tokens,
        coalesce(sum(case when outcome = 'success' then 1 else 0 end), 0)::BIGINT
            as success_count,
        coalesce(sum(case when outcome <> 'success' then 1 else 0 end), 0)::BIGINT
            as failure_count,
        avg(latency_ms) as avg_latency_ms,
        avg(time_to_first_token_ms) as avg_ttft_ms,
        avg(stream_duration_ms) as avg_stream_duration_ms,
        coalesce(sum(retry_count), 0)::BIGINT as retry_count,
        coalesce(sum(fallback_count), 0)::BIGINT as fallback_count,
        coalesce(sum(fallback_tokens), 0)::BIGINT as fallback_tokens,
        avg(queue_ms) as avg_queue_ms,
        avg(prefill_ms) as avg_prefill_ms,
        avg(decode_ms) as avg_decode_ms,
        avg(kv_cache_usage) as avg_kv_cache_usage,
        coalesce(sum(
            case when queue_ms is not null
                or prefill_ms is not null
                or decode_ms is not null
                or kv_cache_usage is not null
            then 1 else 0 end
        ), 0)::BIGINT as vllm_metrics_count
    """


def _dimension_sql(dimension: AnalyticsDimension) -> tuple[str, str]:
    if dimension == "model":
        return "model_alias", "coalesce(model_alias, '无模型')"
    if dimension == "subject":
        return (
            "subject_id",
            "coalesce(subject_name || ' / ' || subject_login_username, subject_name, subject_login_username, subject_id, '无用户')",
        )
    if dimension == "project":
        return "project_id", "coalesce(project_name, project_id, '无项目')"
    if dimension == "endpoint":
        return "endpoint_family", "endpoint_family"
    if dimension == "outcome":
        return "outcome", "outcome"
    if dimension == "streaming":
        return (
            "case when streaming then 'true' else 'false' end",
            "case when streaming then '流式' else '非流式' end",
        )
    raise ValueError(f"Unsupported analytics dimension: {dimension}")


def _value(item: Any) -> str | None:
    if item is None:
        return None
    return getattr(item, "value", str(item))


def _optional_uuid(item: UUID | None) -> str | None:
    return str(item) if item else None
