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
    """ROUND(AVG(col), 2) —— PG 的 ROUND 需要 numeric，avg 返回 double，先 cast。

    PG 不接受 ROUND(double, int)——必须 cast 成 numeric。输出统一为保留
    2 位小数的数值。
    """
    return func.round(cast(func.avg(column), Numeric), 2)

_VALID_BUCKETS = frozenset({"minute", "hour", "day"})


def _core_metric_columns() -> list:
    """6 个核心 metric。不含 cached_tokens。"""
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
    """18 个 metric，字段顺序与 usage_summary/usage_totals 严格一致。

    cached_tokens 在 success_count 之前，所以 full 不能写成 core + 扩展——
    字段顺序不同。独立定义。
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


def normalize_naive_utc(value: datetime | None) -> datetime | None:
    """Normalize a query-param datetime to naive UTC for comparisons against
    RequestFact timestamps (stored naive UTC).

    Offset-aware inputs (e.g. ``2026-08-15T15:30+08:00`` from a browser) are
    converted to UTC and stripped; naive inputs are assumed to already be UTC,
    preserving backward compatibility for existing API callers.
    """
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _apply_filters(stmt, *, start, end, model, subject_id, project_id,
                   subject_ids=None, project_ids=None):
    """条件应用 where：None 值跳过对应过滤条件。

    subject_ids/project_ids 是列表作用域（个人/项目/团队看板），与单个
    subject_id/project_id 点查共存。"""
    start = normalize_naive_utc(start)
    end = normalize_naive_utc(end)
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
    if subject_ids is not None:
        stmt = stmt.where(col(RequestFact.subject_id).in_(subject_ids))
    if project_ids is not None:
        stmt = stmt.where(col(RequestFact.project_id).in_(project_ids))
    return stmt


def _row_to_dict(row) -> dict:
    """把 SQLAlchemy Row 转 dict，键用 label 名。

    Decimal（来自 round(numeric, 2)）转 float，否则 FastAPI 会把 Decimal
    序列化成 JSON 字符串，前端拿到 "12.30" 而非 12.3。
    """
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in row._mapping.items()}


async def _apply_statement_timeout(session) -> None:
    """Set a per-transaction statement_timeout so a runaway aggregate cannot
    monopolize the shared primary connection. 大窗口聚合可能压垮数据面，
    因此这个保护必须保留。
    SET LOCAL 只影响当前事务，session_dep 的请求级事务正好覆盖一次查询。
    """
    from llm_gateway.core.config import get_settings

    seconds = get_settings().analytics_statement_timeout_seconds
    await session.execute(text(f"SET LOCAL statement_timeout = {int(seconds * 1000)}"))


def _ensure_utc_iso(value) -> str:
    """保证返回带时区的 ISO 字符串。"""
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
    """全量 18-metric 单行聚合。无数据（request_count==0）返回 None。"""
    await _apply_statement_timeout(session)
    stmt = _apply_filters(
        select(*_full_metric_columns()).select_from(RequestFact),
        start=start, end=end, model=model, subject_id=subject_id, project_id=project_id,
    )
    row = (await session.execute(stmt)).one()
    d = _row_to_dict(row)
    return None if d["request_count"] == 0 else d


async def scoped_usage_summary(session, *, start=None, end=None,
                               subject_ids=None, project_ids=None) -> dict:
    """6-key 聚合（个人 / 管理者看板用）。空 id 列表直接返回零值。

    这是 auth 路由用量汇总的唯一实现——此前 api/auth.py 里有一份与
    usage_totals 表达式逐字段镜像的私有拷贝，双实现已经漂移过一次。
    """
    empty = {
        "request_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "success_count": 0,
        "failure_count": 0,
    }
    if subject_ids is not None and not subject_ids:
        return dict(empty)
    if project_ids is not None and not project_ids:
        return dict(empty)
    await _apply_statement_timeout(session)
    stmt = _apply_filters(
        select(*_core_metric_columns()).select_from(RequestFact),
        start=start, end=end, model=None, subject_id=None, project_id=None,
        subject_ids=subject_ids, project_ids=project_ids,
    )
    row = _row_to_dict((await session.execute(stmt)).one())
    return {key: int(row.get(key) or 0) for key in empty}


async def scoped_usage_ranking(session, *, start=None, end=None,
                               subject_ids=None, project_ids=None,
                               model=None, limit=20) -> list[dict]:
    """按 subject 分组排名，作用域由显式传入的 id 列表决定（项目/团队）。

    subject_id 统一转 str，subject_id IS NULL 的行固定排除（与
    usage_ranking 一致）。空 id 列表短路返回 []。
    """
    if subject_ids is not None and not subject_ids:
        return []
    if project_ids is not None and not project_ids:
        return []
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
        subject_ids=subject_ids, project_ids=project_ids,
    )
    stmt = stmt.where(col(RequestFact.subject_id).isnot(None)).group_by(
        col(RequestFact.subject_id), col(Subject.login_username), col(Subject.name)
    ).order_by(desc(text("total_tokens")), desc(text("request_count"))).limit(int(limit))
    rows = (await session.execute(stmt)).all()
    items = [_row_to_dict(row) for row in rows]
    for item in items:
        item["subject_id"] = str(item["subject_id"])
        for key in ("request_count", "prompt_tokens", "completion_tokens",
                    "total_tokens", "success_count", "failure_count"):
            item[key] = int(item.get(key) or 0)
    return items


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
    items = [_row_to_dict(row) for row in rows]
    await _attach_summary_names(session, items)
    return items


async def _attach_summary_names(session, items: list[dict]) -> None:
    """为本页聚合行批量内嵌用户/项目显示名。

    分页化后前端不再持有全量用户/项目清单，展示名必须随行走。
    只对当前页出现的 id 各发一次 IN 查询，代价与页大小成正比。
    """
    subject_ids = {item["subject_id"] for item in items if item.get("subject_id")}
    project_ids = {item["project_id"] for item in items if item.get("project_id")}
    subjects: dict = {}
    if subject_ids:
        result = await session.execute(
            select(Subject.id, Subject.name, Subject.login_username).where(
                col(Subject.id).in_(subject_ids)
            )
        )
        for row in result.all():
            subjects[row.id] = (row[1], row[2])
    projects: dict = {}
    if project_ids:
        result = await session.execute(
            select(Project.id, Project.name).where(col(Project.id).in_(project_ids))
        )
        for row in result.all():
            projects[row.id] = row[1]
    for item in items:
        subject = subjects.get(item.get("subject_id"))
        item["subject_name"] = subject[0] if subject else None
        item["subject_login_username"] = subject[1] if subject else None
        item["project_name"] = projects.get(item.get("project_id"))


async def usage_ranking(session, *, start=None, end=None, model=None,
                        limit=20) -> list[dict]:
    """按 subject 分组排名。core 6 metric（不含 cached_tokens/延迟/vllm），
    JOIN subjects 取 name/login_username。固定过滤 subject_id IS NOT NULL。"""
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
    """返回 (dim_selects, join_target_or_None, group_by_columns)。"""
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
    bucket∈{minute,hour,day}。返回的 bucket_start 统一转 ISO 字符串。"""
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
    """按 dimension 分组，full 18-metric。dimension_id 统一转 str。
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
