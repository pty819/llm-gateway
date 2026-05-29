from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from threading import Lock
from uuid import UUID

import duckdb

from llm_gateway.core.config import Settings

logger = logging.getLogger(__name__)

_METRICS_SQL = """\
COUNT(id) AS request_count,
COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
COALESCE(SUM(COALESCE(total_tokens, COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0), 0)), 0) AS total_tokens,
COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
COALESCE(SUM(CASE WHEN outcome ILIKE 'success' THEN 1 ELSE 0 END), 0) AS success_count,
COALESCE(SUM(CASE WHEN NOT outcome ILIKE 'success' THEN 1 ELSE 0 END), 0) AS failure_count,
ROUND(AVG(latency_ms), 2) AS avg_latency_ms,
ROUND(AVG(time_to_first_token_ms), 2) AS avg_ttft_ms,
ROUND(AVG(stream_duration_ms), 2) AS avg_stream_duration_ms,
COALESCE(SUM(retry_count), 0) AS retry_count,
COALESCE(SUM(fallback_count), 0) AS fallback_count,
COALESCE(SUM(fallback_tokens), 0) AS fallback_tokens,
ROUND(AVG(queue_ms), 2) AS avg_queue_ms,
ROUND(AVG(prefill_ms), 2) AS avg_prefill_ms,
ROUND(AVG(decode_ms), 2) AS avg_decode_ms,
ROUND(AVG(kv_cache_usage), 2) AS avg_kv_cache_usage,
COALESCE(SUM(CASE WHEN queue_ms IS NOT NULL OR prefill_ms IS NOT NULL OR decode_ms IS NOT NULL OR kv_cache_usage IS NOT NULL THEN 1 ELSE 0 END), 0) AS vllm_metrics_count"""


def _build_filters(
    start: datetime | None,
    end: datetime | None,
    model: str | None,
    subject_id: UUID | str | None,
    project_id: UUID | str | None,
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if start is not None:
        clauses.append("started_at >= ?")
        params.append(start)
    if end is not None:
        clauses.append("started_at < ?")
        params.append(end)
    if model is not None:
        clauses.append("model_alias = ?")
        params.append(model)
    if subject_id is not None:
        clauses.append("subject_id = ?")
        params.append(str(subject_id))
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(str(project_id))
    return clauses, params


def _to_libpg_dsn(database_url: str) -> str:
    """Convert a SQLAlchemy asyncpg URL to a libpq-compatible connection string."""
    url = database_url
    if "+" in url:
        url = url.replace("+asyncpg", "")
    return url


class DuckDBAnalytics:
    def __init__(self, settings: Settings) -> None:
        self._lock = Lock()
        dsn = _to_libpg_dsn(settings.database_url)
        self._con = duckdb.connect()
        self._con.execute("INSTALL postgres")
        self._con.execute("LOAD postgres")
        self._con.execute(
            f"ATTACH '{dsn}' AS pg (TYPE postgres, READ_ONLY)"
        )
        self._con.execute("SET pg_connection_limit=8")
        logger.info("DuckDB analytics attached to PostgreSQL at %s", dsn.split("@")[-1] if "@" in dsn else dsn)

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass

    def _query(self, sql: str, params: list[object] | None = None) -> list[dict]:
        with self._lock:
            result = self._con.execute(sql, params or [])
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
        return [
            {k: _serialize_value(k, v) for k, v in zip(columns, row)}
            for row in rows
        ]

    async def query(self, sql: str, params: list[object] | None = None) -> list[dict]:
        return await asyncio.to_thread(self._query, sql, params)

    async def usage_totals(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        model: str | None = None,
        subject_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> dict | None:
        clauses, params = _build_filters(start, end, model, subject_id, project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {_METRICS_SQL} FROM pg.public.request_facts {where}"
        rows = await self.query(sql, params)
        if not rows or rows[0]["request_count"] == 0:
            return None
        return rows[0]

    async def usage_summary(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        model: str | None = None,
        subject_id: UUID | None = None,
        project_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        clauses, params = _build_filters(start, end, model, subject_id, project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""\
SELECT model_alias, subject_id, project_id,
       {_METRICS_SQL}
FROM pg.public.request_facts {where}
GROUP BY model_alias, subject_id, project_id
ORDER BY total_tokens DESC, request_count DESC"""
        if limit is not None:
            sql += f"\nLIMIT {int(limit)}"
        return await self.query(sql, params)

    async def usage_ranking(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        model: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        clauses, params = _build_filters(start, end, model, None, None)
        clauses.append("rf.subject_id IS NOT NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""\
SELECT rf.subject_id, s.login_username, COALESCE(s.name, '无用户') AS subject_name,
       COUNT(rf.id) AS request_count,
       COALESCE(SUM(rf.prompt_tokens), 0) AS prompt_tokens,
       COALESCE(SUM(rf.completion_tokens), 0) AS completion_tokens,
       COALESCE(SUM(COALESCE(rf.total_tokens, COALESCE(rf.prompt_tokens, 0) + COALESCE(rf.completion_tokens, 0), 0)), 0) AS total_tokens
FROM pg.public.request_facts rf
LEFT JOIN pg.public.subjects s ON rf.subject_id = s.id
{where}
GROUP BY rf.subject_id, s.login_username, s.name
ORDER BY total_tokens DESC, request_count DESC
LIMIT {int(limit)}"""
        return await self.query(sql, params)

    async def time_buckets(
        self,
        bucket: str = "hour",
        start: datetime | None = None,
        end: datetime | None = None,
        model: str | None = None,
        subject_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> list[dict]:
        clauses, params = _build_filters(start, end, model, subject_id, project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""\
SELECT date_trunc('{bucket}', started_at) AS bucket_start,
       {_METRICS_SQL}
FROM pg.public.request_facts {where}
GROUP BY bucket_start
ORDER BY bucket_start DESC"""
        rows = await self.query(sql, params)
        for row in rows:
            if row.get("bucket_start") is not None:
                bs = row["bucket_start"]
                if isinstance(bs, datetime):
                    if bs.tzinfo is None:
                        bs = bs.replace(tzinfo=timezone.utc)
                    row["bucket_start"] = bs.isoformat()
                elif isinstance(bs, str) and "+" not in bs and bs.endswith("Z") is False:
                    row["bucket_start"] = bs + "+00:00"
        return rows

    async def drilldown(
        self,
        dimension: str = "model",
        start: datetime | None = None,
        end: datetime | None = None,
        model: str | None = None,
        subject_id: UUID | None = None,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses, params = _build_filters(start, end, model, subject_id, project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        dim_sql, join_sql, group_sql = _dimension_sql(dimension)
        sql = f"""\
SELECT {dim_sql},
       {_METRICS_SQL}
FROM pg.public.request_facts rf
{join_sql}
{where}
GROUP BY {group_sql}
ORDER BY request_count DESC
LIMIT {int(limit)}"""
        rows = await self.query(sql, params)
        for row in rows:
            if row.get("dimension_id") is not None:
                row["dimension_id"] = str(row["dimension_id"])
        return rows

    async def own_usage_summary(
        self,
        subject_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict:
        clauses, params = _build_filters(start, end, None, subject_id, None)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""\
SELECT COUNT(id) AS request_count,
       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
       COALESCE(SUM(COALESCE(total_tokens, COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0), 0)), 0) AS total_tokens,
       COALESCE(SUM(CASE WHEN outcome ILIKE 'success' THEN 1 ELSE 0 END), 0) AS success_count,
       COALESCE(SUM(CASE WHEN NOT outcome ILIKE 'success' THEN 1 ELSE 0 END), 0) AS failure_count
FROM pg.public.request_facts {where}"""
        rows = await self.query(sql, params)
        return rows[0] if rows else {
            "request_count": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "success_count": 0, "failure_count": 0,
        }


def _dimension_sql(dimension: str) -> tuple[str, str, str]:
    if dimension == "subject":
        return (
            "rf.subject_id AS dimension_id, COALESCE(s.name, s.login_username, '无用户') AS dimension_label",
            "LEFT JOIN pg.public.subjects s ON rf.subject_id = s.id",
            "rf.subject_id, s.name, s.login_username",
        )
    if dimension == "project":
        return (
            "rf.project_id AS dimension_id, COALESCE(p.name, '无项目') AS dimension_label",
            "LEFT JOIN pg.public.projects p ON rf.project_id = p.id",
            "rf.project_id, p.name",
        )
    if dimension == "endpoint":
        return (
            "rf.endpoint_family AS dimension_id, rf.endpoint_family AS dimension_label",
            "",
            "rf.endpoint_family",
        )
    if dimension == "outcome":
        return (
            "rf.outcome AS dimension_id, rf.outcome AS dimension_label",
            "",
            "rf.outcome",
        )
    if dimension == "streaming":
        return (
            "rf.streaming AS dimension_id, CASE WHEN rf.streaming THEN '流式' ELSE '非流式' END AS dimension_label",
            "",
            "rf.streaming",
        )
    return (
        "rf.model_alias AS dimension_id, COALESCE(rf.model_alias, '无模型') AS dimension_label",
        "",
        "rf.model_alias",
    )


def _serialize_value(key: str, value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


_analytics: DuckDBAnalytics | None = None


def init_analytics(settings: Settings) -> DuckDBAnalytics:
    global _analytics
    _analytics = DuckDBAnalytics(settings)
    return _analytics


def get_analytics() -> DuckDBAnalytics:
    if _analytics is None:
        raise RuntimeError("DuckDB analytics not initialized")
    return _analytics


def close_analytics() -> None:
    global _analytics
    if _analytics is not None:
        _analytics.close()
        _analytics = None
