from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import case, cast, desc, func, select, text
from sqlalchemy import Numeric, Text
from sqlmodel import col

from llm_gateway.db.models import Project, RequestFact, Subject


# outcome 是 PG enum，func.lower 不能直接作用于 enum，必须先 cast 成 text。
_SUCCESS_CASE = case(
    (func.lower(cast(col(RequestFact.outcome), Text)) == "success", 1), else_=0
)


def _round2_avg(column):
    """ROUND(AVG(col), 2) —— PG 的 round 需 numeric，avg 返回 double，先 cast。

    DuckDB 接受 ROUND(double, int)，PG 不接受——必须 cast 成 numeric。
    对齐 DuckDB 输出（都是保留 2 位小数的数值）。
    """
    return func.round(cast(func.avg(column), Numeric), 2)

_VALID_BUCKETS = frozenset({"minute", "hour", "day"})


def _core_metric_columns() -> list:
    """6 个核心 metric，对齐 DuckDB _CORE_METRICS_SQL。不含 cached_tokens。"""
    total_tokens_expr = func.coalesce(
        RequestFact.total_tokens,
        func.coalesce(RequestFact.prompt_tokens, 0) + func.coalesce(RequestFact.completion_tokens, 0),
        0,
    )
    return [
        func.count(col(RequestFact.id)).label("request_count"),
        func.coalesce(func.sum(RequestFact.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(RequestFact.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(total_tokens_expr), 0).label("total_tokens"),
        func.coalesce(func.sum(_SUCCESS_CASE), 0).label("success_count"),
        func.coalesce(func.sum(case(
            (func.lower(cast(col(RequestFact.outcome), Text)) != "success", 1), else_=0
        )), 0).label("failure_count"),
    ]


def _full_metric_columns() -> list:
    """18 个 metric，对齐 DuckDB _METRICS_SQL 的精确字段顺序。

    cached_tokens 在 success_count 之前（与 DuckDB 一致），所以 full 不能写成
    core + 扩展——字段顺序不同。独立定义。
    """
    total_tokens_expr = func.coalesce(
        RequestFact.total_tokens,
        func.coalesce(RequestFact.prompt_tokens, 0) + func.coalesce(RequestFact.completion_tokens, 0),
        0,
    )
    return [
        func.count(col(RequestFact.id)).label("request_count"),
        func.coalesce(func.sum(RequestFact.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(RequestFact.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(total_tokens_expr), 0).label("total_tokens"),
        func.coalesce(func.sum(RequestFact.cached_tokens), 0).label("cached_tokens"),
        func.coalesce(func.sum(_SUCCESS_CASE), 0).label("success_count"),
        func.coalesce(func.sum(case(
            (func.lower(cast(col(RequestFact.outcome), Text)) != "success", 1), else_=0
        )), 0).label("failure_count"),
        _round2_avg(RequestFact.latency_ms).label("avg_latency_ms"),
        _round2_avg(RequestFact.time_to_first_token_ms).label("avg_ttft_ms"),
        _round2_avg(RequestFact.stream_duration_ms).label("avg_stream_duration_ms"),
        func.coalesce(func.sum(RequestFact.retry_count), 0).label("retry_count"),
        func.coalesce(func.sum(RequestFact.fallback_count), 0).label("fallback_count"),
        func.coalesce(func.sum(RequestFact.fallback_tokens), 0).label("fallback_tokens"),
        _round2_avg(RequestFact.queue_ms).label("avg_queue_ms"),
        _round2_avg(RequestFact.prefill_ms).label("avg_prefill_ms"),
        _round2_avg(RequestFact.decode_ms).label("avg_decode_ms"),
        _round2_avg(RequestFact.kv_cache_usage).label("avg_kv_cache_usage"),
        func.coalesce(func.sum(case(
            (col(RequestFact.queue_ms).isnot(None)
             | col(RequestFact.prefill_ms).isnot(None)
             | col(RequestFact.decode_ms).isnot(None)
             | col(RequestFact.kv_cache_usage).isnot(None), 1),
            else_=0,
        )), 0).label("vllm_metrics_count"),
    ]


def _apply_filters(stmt, *, start, end, model, subject_id, project_id):
    """条件应用 where，对齐 DuckDB _build_filters 的 None-skip 语义。"""
    if start is not None:
        stmt = stmt.where(col(RequestFact.started_at) >= start)
    if end is not None:
        stmt = stmt.where(col(RequestFact.started_at) < end)
    if model is not None:
        stmt = stmt.where(col(RequestFact.model_alias) == model)
    if subject_id is not None:
        stmt = stmt.where(col(RequestFact.subject_id) == subject_id)
    if project_id is not None:
        stmt = stmt.where(col(RequestFact.project_id) == project_id)
    return stmt


def _row_to_dict(row) -> dict:
    """把 SQLAlchemy Row 转 dict，键用 label 名。

    Decimal（来自 round(numeric, 2)）转 float，对齐 DuckDB ROUND(AVG(double),2)
    的 float 输出——否则 FastAPI 会把 Decimal 序列化成 JSON 字符串，前端拿到
    "12.30" 而非 12.3。
    """
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in row._mapping.items()}


async def _apply_statement_timeout(session) -> None:
    """Set a per-transaction statement_timeout so a runaway aggregate cannot
    monopolize the shared primary connection. 对齐 DuckDB 的 wait_for(timeout=15s)
    守卫——DuckDB 删除后必须保留这个保护，否则大窗口聚合会压垮数据面。
    SET LOCAL 只影响当前事务，session_dep 的请求级事务正好覆盖一次查询。
    """
    from llm_gateway.core.config import get_settings

    seconds = get_settings().analytics_statement_timeout_seconds
    await session.execute(text(f"SET LOCAL statement_timeout = {int(seconds * 1000)}"))


def _ensure_utc_iso(value) -> str:
    """对齐 DuckDB _ensure_utc_iso：保证返回带时区的 ISO 字符串。"""
    if isinstance(value, str):
        if "+" not in value and not value.endswith("Z"):
            return value + "+00:00"
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


async def usage_totals(session, *, start=None, end=None, model=None,
                       subject_id=None, project_id=None) -> dict | None:
    """全量 18-metric 单行聚合。无数据返回 None（对齐 DuckDB：request_count==0→None）。"""
    await _apply_statement_timeout(session)
    stmt = _apply_filters(
        select(*_full_metric_columns()).select_from(RequestFact),
        start=start, end=end, model=model, subject_id=subject_id, project_id=project_id,
    )
    row = (await session.execute(stmt)).one()
    d = _row_to_dict(row)
    return None if d["request_count"] == 0 else d


async def usage_summary(session, *, start=None, end=None, model=None,
                        subject_id=None, project_id=None, limit=None) -> list[dict]:
    """按 (model_alias, subject_id, project_id) 分组，full 18-metric。
    排序 total_tokens DESC, request_count DESC。limit 可选（None=不限制）。"""
    await _apply_statement_timeout(session)
    stmt = _apply_filters(
        select(
            col(RequestFact.model_alias),
            col(RequestFact.subject_id),
            col(RequestFact.project_id),
            *_full_metric_columns(),
        ).select_from(RequestFact),
        start=start, end=end, model=model, subject_id=subject_id, project_id=project_id,
    )
    stmt = stmt.group_by(
        col(RequestFact.model_alias), col(RequestFact.subject_id), col(RequestFact.project_id)
    ).order_by(desc(text("total_tokens")), desc(text("request_count")))
    if limit is not None:
        stmt = stmt.limit(int(limit))
    rows = (await session.execute(stmt)).all()
    return [_row_to_dict(row) for row in rows]


async def usage_ranking(session, *, start=None, end=None, model=None,
                        limit=20) -> list[dict]:
    """按 subject 分组排名。core 6 metric（不含 cached_tokens/延迟/vllm），
    JOIN subjects 取 name/login_username。固定过滤 subject_id IS NOT NULL（对齐 DuckDB）。"""
    await _apply_statement_timeout(session)
    stmt = _apply_filters(
        select(
            col(RequestFact.subject_id).label("subject_id"),
            col(Subject.login_username).label("login_username"),
            func.coalesce(col(Subject.name), "无用户").label("subject_name"),
            *_core_metric_columns(),
        ).select_from(RequestFact)
        .outerjoin(Subject, RequestFact.subject_id == Subject.id),
        start=start, end=end, model=model, subject_id=None, project_id=None,
    )
    stmt = stmt.where(col(RequestFact.subject_id).isnot(None)).group_by(
        col(RequestFact.subject_id), col(Subject.login_username), col(Subject.name)
    ).order_by(desc(text("total_tokens")), desc(text("request_count"))).limit(int(limit))
    rows = (await session.execute(stmt)).all()
    return [_row_to_dict(row) for row in rows]


def _dimension_columns(dimension: str):
    """对齐 DuckDB _dimension_sql。返回 (dim_selects, join_target_or_None, group_by_columns)。"""
    if dimension == "subject":
        return (
            [col(RequestFact.subject_id).label("dimension_id"),
             func.coalesce(col(Subject.name), col(Subject.login_username), "无用户").label("dimension_label")],
            Subject,
            [col(RequestFact.subject_id), col(Subject.name), col(Subject.login_username)],
        )
    if dimension == "project":
        return (
            [col(RequestFact.project_id).label("dimension_id"),
             func.coalesce(col(Project.name), "无项目").label("dimension_label")],
            Project,
            [col(RequestFact.project_id), col(Project.name)],
        )
    if dimension == "endpoint":
        return (
            [col(RequestFact.endpoint_family).label("dimension_id"),
             col(RequestFact.endpoint_family).label("dimension_label")],
            None,
            [col(RequestFact.endpoint_family)],
        )
    if dimension == "outcome":
        return (
            [col(RequestFact.outcome).label("dimension_id"),
             col(RequestFact.outcome).label("dimension_label")],
            None,
            [col(RequestFact.outcome)],
        )
    if dimension == "streaming":
        return (
            [col(RequestFact.streaming).label("dimension_id"),
             case((col(RequestFact.streaming) == True, "流式"), else_="非流式").label("dimension_label")],
            None,
            [col(RequestFact.streaming)],
        )
    # default: model
    return (
        [col(RequestFact.model_alias).label("dimension_id"),
         func.coalesce(col(RequestFact.model_alias), "无模型").label("dimension_label")],
        None,
        [col(RequestFact.model_alias)],
    )


async def time_buckets(session, *, bucket="hour", start=None, end=None,
                       model=None, subject_id=None, project_id=None) -> list[dict]:
    """按 date_trunc(bucket, started_at) 分组，full 18-metric。
    bucket∈{minute,hour,day}。返回的 bucket_start 统一转 ISO 字符串（对齐 DuckDB）。"""
    if bucket not in _VALID_BUCKETS:
        raise ValueError(f"Invalid bucket: {bucket!r}")
    await _apply_statement_timeout(session)
    stmt = _apply_filters(
        select(
            func.date_trunc(bucket, col(RequestFact.started_at)).label("bucket_start"),
            *_full_metric_columns(),
        ).select_from(RequestFact),
        start=start, end=end, model=model, subject_id=subject_id, project_id=project_id,
    )
    stmt = stmt.group_by(text("bucket_start")).order_by(desc(text("bucket_start")))
    rows = (await session.execute(stmt)).all()
    result = [_row_to_dict(row) for row in rows]
    for row in result:
        if row.get("bucket_start") is not None:
            row["bucket_start"] = _ensure_utc_iso(row["bucket_start"])
    return result


async def drilldown(session, *, dimension="model", start=None, end=None,
                    model=None, subject_id=None, project_id=None, limit=100) -> list[dict]:
    """按 dimension 分组，full 18-metric。dimension_id 转 str（对齐 DuckDB）。
    维度：model/subject/project/endpoint/outcome/streaming。"""
    dim_selects, join_target, group_by = _dimension_columns(dimension)
    await _apply_statement_timeout(session)
    stmt = _apply_filters(
        select(*dim_selects, *_full_metric_columns()).select_from(RequestFact),
        start=start, end=end, model=model, subject_id=subject_id, project_id=project_id,
    )
    if join_target is Subject:
        stmt = stmt.outerjoin(Subject, RequestFact.subject_id == Subject.id)
    elif join_target is Project:
        stmt = stmt.outerjoin(Project, RequestFact.project_id == Project.id)
    stmt = stmt.group_by(*group_by).order_by(desc(text("request_count"))).limit(int(limit))
    rows = (await session.execute(stmt)).all()
    result = [_row_to_dict(row) for row in rows]
    for row in result:
        if row.get("dimension_id") is not None:
            row["dimension_id"] = str(row["dimension_id"])
    return result
