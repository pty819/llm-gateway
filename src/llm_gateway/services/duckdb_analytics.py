from __future__ import annotations

import asyncio
import gzip
import logging
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse
from uuid import UUID

import duckdb

from llm_gateway.core.config import Settings

logger = logging.getLogger(__name__)

_TABLE = "pg.public.request_facts"
_DUCKDB_VERSION = "v1.5.3"
_POSTGRES_EXTENSION_NAME = "postgres_scanner"
_ROOT = Path(__file__).resolve().parents[3]
_VENDOR_EXTENSION_ROOT = _ROOT / "vendor" / "duckdb" / "extensions"

_SUCCESS_CASE = "CASE WHEN LOWER(rf.outcome) = 'success' THEN 1 ELSE 0 END"

_METRICS_SQL = f"""\
COUNT(rf.id) AS request_count,
COALESCE(SUM(rf.prompt_tokens), 0) AS prompt_tokens,
COALESCE(SUM(rf.completion_tokens), 0) AS completion_tokens,
COALESCE(SUM(COALESCE(rf.total_tokens, COALESCE(rf.prompt_tokens, 0) + COALESCE(rf.completion_tokens), 0)), 0) AS total_tokens,
COALESCE(SUM(rf.cached_tokens), 0) AS cached_tokens,
COALESCE(SUM({_SUCCESS_CASE}), 0) AS success_count,
COALESCE(SUM(CASE WHEN LOWER(rf.outcome) != 'success' THEN 1 ELSE 0 END), 0) AS failure_count,
ROUND(AVG(rf.latency_ms), 2) AS avg_latency_ms,
ROUND(AVG(rf.time_to_first_token_ms), 2) AS avg_ttft_ms,
ROUND(AVG(rf.stream_duration_ms), 2) AS avg_stream_duration_ms,
COALESCE(SUM(rf.retry_count), 0) AS retry_count,
COALESCE(SUM(rf.fallback_count), 0) AS fallback_count,
COALESCE(SUM(rf.fallback_tokens), 0) AS fallback_tokens,
ROUND(AVG(rf.queue_ms), 2) AS avg_queue_ms,
ROUND(AVG(rf.prefill_ms), 2) AS avg_prefill_ms,
ROUND(AVG(rf.decode_ms), 2) AS avg_decode_ms,
ROUND(AVG(rf.kv_cache_usage), 2) AS avg_kv_cache_usage,
COALESCE(SUM(CASE WHEN rf.queue_ms IS NOT NULL OR rf.prefill_ms IS NOT NULL OR rf.decode_ms IS NOT NULL OR rf.kv_cache_usage IS NOT NULL THEN 1 ELSE 0 END), 0) AS vllm_metrics_count"""

_CORE_METRICS_SQL = f"""\
COUNT(rf.id) AS request_count,
COALESCE(SUM(rf.prompt_tokens), 0) AS prompt_tokens,
COALESCE(SUM(rf.completion_tokens), 0) AS completion_tokens,
COALESCE(SUM(COALESCE(rf.total_tokens, COALESCE(rf.prompt_tokens, 0) + COALESCE(rf.completion_tokens), 0)), 0) AS total_tokens,
COALESCE(SUM({_SUCCESS_CASE}), 0) AS success_count,
COALESCE(SUM(CASE WHEN LOWER(rf.outcome) != 'success' THEN 1 ELSE 0 END), 0) AS failure_count"""

_VALID_BUCKETS = frozenset(
    {"minute", "hour", "day", "week", "month", "year", "quarter"}
)


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


def _where(clauses: list[str]) -> str:
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def _parse_pg_dsn(database_url: str) -> tuple[str, str, int, str, str]:
    """Parse a SQLAlchemy database URL into (host, database, port, user, password)."""
    parsed = urlparse(database_url)
    scheme = parsed.scheme.split("+")[0]
    if scheme not in ("postgresql", "postgres"):
        raise ValueError(f"Expected postgresql:// DSN, got {parsed.scheme}://")
    return (
        parsed.hostname or "localhost",
        parsed.path.lstrip("/") or "postgres",
        parsed.port or 5432,
        parsed.username or "",
        parsed.password or "",
    )


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


class DuckDBAnalytics:
    def __init__(self, settings: Settings) -> None:
        self._lock = Lock()
        self._closed = False
        # Prefer a dedicated analytics DSN (e.g. a read replica) so heavy
        # analytical scans do not compete with the data plane on the primary.
        analytics_dsn = settings.analytics_database_url or settings.database_url
        self._statement_timeout = settings.analytics_statement_timeout_seconds
        host, database, port, user, password = _parse_pg_dsn(analytics_dsn)
        display = f"{host}:{port}/{database}"
        try:
            self._con = duckdb.connect()
            _load_postgres_extension(self._con)
            self._con.execute(
                f"CREATE SECRET pg_secret (TYPE postgres,"
                f" HOST '{_sql_escape(host)}',"
                f" PORT {port},"
                f" DATABASE '{_sql_escape(database)}',"
                f" USER '{_sql_escape(user)}',"
                f" PASSWORD '{_sql_escape(password)}')"
            )
            self._con.execute(
                "ATTACH '' AS pg (TYPE postgres, READ_ONLY, SECRET pg_secret)"
            )
            self._con.execute("SET pg_connection_limit=8")
        except Exception:
            self._con.close()
            raise
        logger.info("DuckDB analytics attached to PostgreSQL at %s", display)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self._con.close()
            except Exception as exc:
                logger.debug("Error closing DuckDB connection: %s", exc)

    def _query(self, sql: str, params: list[object] | None = None) -> list[dict]:
        with self._lock:
            if self._closed:
                raise RuntimeError("DuckDB analytics connection closed")
            result = self._con.execute(sql, params or [])
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
        return [{k: _serialize_value(v) for k, v in zip(columns, row)} for row in rows]

    async def query(self, sql: str, params: list[object] | None = None) -> list[dict]:
        # Bound a runaway aggregate so one slow query cannot hang the analytics
        # surface (the DuckDB connection is single/locked). On timeout we surface
        # an error to the caller rather than waiting indefinitely.
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._query, sql, params),
                timeout=self._statement_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "analytics query exceeded %.1fs and was cancelled", self._statement_timeout
            )
            raise

    async def usage_totals(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        model: str | None = None,
        subject_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> dict | None:
        clauses, params = _build_filters(start, end, model, subject_id, project_id)
        sql = f"SELECT {_METRICS_SQL} FROM {_TABLE} rf {_where(clauses)}"
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
        limit_clause = f"\nLIMIT {int(limit)}" if limit is not None else ""
        sql = f"""\
SELECT model_alias, subject_id, project_id,
       {_METRICS_SQL}
FROM {_TABLE} rf {_where(clauses)}
GROUP BY rf.model_alias, rf.subject_id, rf.project_id
ORDER BY total_tokens DESC, request_count DESC{limit_clause}"""
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
        sql = f"""\
SELECT rf.subject_id, s.login_username, COALESCE(s.name, '无用户') AS subject_name,
       COUNT(rf.id) AS request_count,
       COALESCE(SUM(rf.prompt_tokens), 0) AS prompt_tokens,
       COALESCE(SUM(rf.completion_tokens), 0) AS completion_tokens,
       COALESCE(SUM(COALESCE(rf.total_tokens, COALESCE(rf.prompt_tokens, 0) + COALESCE(rf.completion_tokens), 0)), 0) AS total_tokens,
       COALESCE(SUM(CASE WHEN LOWER(rf.outcome) = 'success' THEN 1 ELSE 0 END), 0) AS success_count,
       COALESCE(SUM(CASE WHEN LOWER(rf.outcome) != 'success' THEN 1 ELSE 0 END), 0) AS failure_count
FROM {_TABLE} rf
LEFT JOIN pg.public.subjects s ON rf.subject_id = s.id
{_where(clauses)}
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
        if bucket not in _VALID_BUCKETS:
            raise ValueError(f"Invalid bucket: {bucket!r}")
        clauses, params = _build_filters(start, end, model, subject_id, project_id)
        sql = f"""\
SELECT date_trunc('{bucket}', rf.started_at) AS bucket_start,
       {_METRICS_SQL}
FROM {_TABLE} rf {_where(clauses)}
GROUP BY bucket_start
ORDER BY bucket_start DESC"""
        rows = await self.query(sql, params)
        for row in rows:
            if row.get("bucket_start") is not None:
                row["bucket_start"] = _ensure_utc_iso(row["bucket_start"])
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
        dim_sql, join_sql, group_sql = _dimension_sql(dimension)
        sql = f"""\
SELECT {dim_sql},
       {_METRICS_SQL}
FROM {_TABLE} rf
{join_sql}
{_where(clauses)}
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
        sql = f"SELECT {_CORE_METRICS_SQL} FROM {_TABLE} rf {_where(clauses)}"
        rows = await self.query(sql, params)
        return (
            rows[0]
            if rows
            else {
                "request_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "success_count": 0,
                "failure_count": 0,
            }
        )


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


def _serialize_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _ensure_utc_iso(value)
    return value


def _load_postgres_extension(connection) -> None:
    local_extension = _ensure_local_postgres_extension()
    if local_extension is not None:
        connection.execute(f"LOAD '{_sql_escape(local_extension.as_posix())}'")
        return
    connection.execute("INSTALL postgres")
    connection.execute("LOAD postgres")


def _ensure_local_postgres_extension() -> Path | None:
    duckdb_platform = _duckdb_platform()
    if duckdb_platform is None:
        return None

    source = (
        _VENDOR_EXTENSION_ROOT
        / _DUCKDB_VERSION
        / duckdb_platform
        / f"{_POSTGRES_EXTENSION_NAME}.duckdb_extension.gz"
    )
    if not source.exists():
        return None

    target = _duckdb_extension_dir(duckdb_platform) / (
        f"{_POSTGRES_EXTENSION_NAME}.duckdb_extension"
    )
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".duckdb_extension.tmp")
    with gzip.open(source, "rb") as src, temp.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    temp.replace(target)
    return target


def _duckdb_extension_dir(duckdb_platform: str) -> Path:
    return Path.home() / ".duckdb" / "extensions" / _DUCKDB_VERSION / duckdb_platform


def _duckdb_platform() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux_amd64"
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "osx_arm64"
    if system == "darwin" and machine in {"x86_64", "amd64"}:
        return "osx_amd64"
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return "windows_amd64"
    return None


def _ensure_utc_iso(value: datetime | str) -> str:
    if isinstance(value, str):
        if "+" not in value and not value.endswith("Z"):
            return value + "+00:00"
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


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
