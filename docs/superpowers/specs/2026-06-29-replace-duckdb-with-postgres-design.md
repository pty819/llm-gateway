# 用 Postgres 替换 DuckDB Analytics

**日期**: 2026-06-29
**状态**: 已确认，待实现

## 背景与目标

当前 admin 的 5 个用量查询端点全部走 DuckDB（通过 postgres 扩展连回 PG 再聚合）。但 PG 的 `request_facts` 表已有 22 个完善的索引（含 `started_at`、`model_alias+started_at`、`subject_id+started_at`、`project_id+started_at` 等复合索引），Postgres 直查的性能显著优于 DuckDB 再连一次的方案。

本次重构将 DuckDB analytics **彻底替换为 Postgres 直查**，删除 DuckDB 服务文件、依赖、vendor 扩展包、生命周期挂载。

## 设计原则

**无功能变化的重构。** 目标是行为完全等价：5 个查询的输入参数、返回字段（含全部 18 个 metric）、排序、过滤语义、边界行为（None 过滤、空结果）全部对齐 DuckDB 现状。前端零改动。只换查询引擎，不改任何功能。

## 范围内

- 新建 `services/analytics.py`，提供 5 个模块级 async 查询函数（SQLAlchemy ORM 风格）
- `api/admin/observability.py` 的 5 个端点改为调用 `analytics.*` 并加 `session_dep`
- 删除 `services/duckdb_analytics.py`、`pyproject.toml` 的 `duckdb==1.5.3`、`vendor/duckdb/`
- `main.py` 移除 `init_analytics`/`close_analytics` 生命周期
- `api/auth.py` 移除未使用的 `get_analytics` import

## 范围外

- **`api/auth.py` 的 `_usage_ranking_from_postgres` / `_usage_summary_from_postgres`**：保持原位，不搬到 `analytics.py`。避免 scope 蔓延，等迁移稳定后未来可做独立的"统一查询函数"重构
- **前端**：零改动。字段完全对齐
- **功能精简**：不做。18 个 metric 全保留

## 范围内（补充）：清理冗余索引

既然彻底转向 Postgres 直查，顺手清理 `request_facts` 表上当前查询模式下无用的索引，减少每次插入 request_fact 时的写入开销。当前 22 个索引中大部分从不参与过滤/分组，或被复合索引覆盖。

### 必须保留（6 个）

| 索引 | 理由 |
|---|---|
| `request_facts_pkey` UNIQUE(id) | 主键 |
| `ix_request_facts_request_id` (request_id) | 去重写入路径必需（UNIQUE 查找） |
| `ix_request_facts_started_at` (started_at) | 单独按 started_at 范围查询（time_buckets/drilldown 不带其它过滤时） |
| `ix_request_facts_model_started` (model_alias, started_at) | model + 时间过滤/排序 |
| `ix_request_facts_subject_started` (subject_id, started_at) | subject + 时间 |
| `ix_request_facts_project_started` (project_id, started_at) | project + 时间 |

### 删除（16 个）

| 索引 | 删除理由 |
|---|---|
| `ix_request_facts_started_model` (started_at, model_alias) | 被 `model_started` 替代（model 在前更优，等值过滤 + 范围） |
| `ix_request_facts_started_subject` (started_at, subject_id) | 被 `subject_started` 替代 |
| `ix_request_facts_started_project` (started_at, project_id) | 被 `project_started` 替代 |
| `ix_request_facts_started_request` (started_at, request_id) | request_id 已有单列索引；查询从不组合 started_at + request_id 过滤 |
| `ix_request_facts_started_at_brin` BRIN(started_at) | 被 `ix_request_facts_started_at`（B-tree）覆盖；BRIN 与 B-tree 重复 |
| `ix_request_facts_subject_id` (subject_id) | 被 `subject_started` 复合索引前缀覆盖 |
| `ix_request_facts_project_id` (project_id) | 被 `project_started` 复合索引前缀覆盖 |
| `ix_request_facts_model_alias` (model_alias) | 被 `model_started` 复合索引前缀覆盖 |
| `ix_request_facts_ended_at` (ended_at) | 查询从不按 ended_at 过滤（全用 started_at） |
| `ix_request_facts_usage_source` (usage_source) | 从不用于过滤/分组 |
| `ix_request_facts_outcome` (outcome) | 只在 CASE 聚合出现，从不作为 where 条件 |
| `ix_request_facts_endpoint_family` (endpoint_family) | 只在 drilldown GROUP BY，PG GROUP BY 走 hash/sort 不走索引 |
| `ix_request_facts_streaming` (streaming) | 同上，drilldown GROUP BY |
| `ix_request_facts_error_class` (error_class) | 从不用于查询 |
| `ix_request_facts_subject_type` (subject_type) | 从不用于查询 |
| `ix_request_facts_upstream_target_id` (upstream_target_id) | 从不用于查询（runtime_metrics 用 Redis） |

清理后：**22 → 6**。所有删除通过新 alembic migration `0010` 的 `drop_index` 完成，downgrade 可重建。未来需要时 alembic 加回成本极低。

## 索引覆盖确认

`request_facts` 表现有 22 个索引，本次 5 个查询的过滤组合全部命中复合索引：

| 查询过滤 | 命中索引 |
|---|---|
| `started_at` 范围 | `ix_request_facts_started_at` / `ix_request_facts_started_at_brin` |
| `model_alias + started_at` | `ix_request_facts_model_started` |
| `subject_id + started_at` | `ix_request_facts_subject_started` |
| `project_id + started_at` | `ix_request_facts_project_started` |
| `started_at + model/subject/project` | `ix_request_facts_started_*` 系列 |
| `outcome` / `endpoint_family` / `streaming` | 单列索引 |

聚合无索引扫描负担（PG 直接在索引过滤后的行集上做 SUM/AVG）。

## 模块结构（`services/analytics.py`）

模块级 async 函数，接收 `AsyncSession`，返回 `dict` / `list[dict]`。和项目现有 Postgres 查询范式一致（`_usage_summary_from_postgres` 等）。

### 共享辅助

```python
from sqlalchemy import case, desc, func, select, text
from sqlmodel import col
from llm_gateway.db.models import RequestFact, Subject, Project

_SUCCESS_CASE = case(
    (func.lower(col(RequestFact.outcome)) == "success", 1), else_=0
)
_VALID_BUCKETS = frozenset({"minute", "hour", "day"})


def _core_metric_columns() -> list:
    """6 个核心 metric，对齐 DuckDB _CORE_METRICS_SQL。"""
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
            (func.lower(col(RequestFact.outcome)) != "success", 1), else_=0
        )), 0).label("failure_count"),
    ]


def _full_metric_columns() -> list:
    """18 个 metric，对齐 DuckDB _METRICS_SQL 的精确字段顺序。

    注意 cached_tokens 在 success_count 之前（与 DuckDB 一致），
    所以 full 不能简单写成 core + 扩展——字段顺序不同。独立定义。
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
            (func.lower(col(RequestFact.outcome)) != "success", 1), else_=0
        )), 0).label("failure_count"),
        func.round(func.avg(RequestFact.latency_ms), 2).label("avg_latency_ms"),
        func.round(func.avg(RequestFact.time_to_first_token_ms), 2).label("avg_ttft_ms"),
        func.round(func.avg(RequestFact.stream_duration_ms), 2).label("avg_stream_duration_ms"),
        func.coalesce(func.sum(RequestFact.retry_count), 0).label("retry_count"),
        func.coalesce(func.sum(RequestFact.fallback_count), 0).label("fallback_count"),
        func.coalesce(func.sum(RequestFact.fallback_tokens), 0).label("fallback_tokens"),
        func.round(func.avg(RequestFact.queue_ms), 2).label("avg_queue_ms"),
        func.round(func.avg(RequestFact.prefill_ms), 2).label("avg_prefill_ms"),
        func.round(func.avg(RequestFact.decode_ms), 2).label("avg_decode_ms"),
        func.round(func.avg(RequestFact.kv_cache_usage), 2).label("avg_kv_cache_usage"),
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


def _dimension_columns(dimension: str):
    """对齐 DuckDB _dimension_sql。返回 (dim_select, join_target_or_None, group_by)。"""
    if dimension == "subject":
        return (
            [col(RequestFact.subject_id).label("dimension_id"),
             func.coalesce(col(Subject.name), col(Subject.login_username), "无用户").label("dimension_label")],
            Subject,  # outerjoin target
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
```

注意 `func.round(func.avg(...))` —— DuckDB 的 SQL 也是 `ROUND(AVG(...), 2)`，对齐。

## 5 个查询函数

### 1. `usage_totals`（单行聚合，full 16 metric）

```python
async def usage_totals(session, *, start=None, end=None, model=None,
                       subject_id=None, project_id=None) -> dict | None:
    """全量 16-metric 单行聚合。无数据返回 None（对齐 DuckDB：request_count==0→None）。"""
    stmt = _apply_filters(
        select(*_full_metric_columns()).select_from(RequestFact),
        start=start, end=end, model=model, subject_id=subject_id, project_id=project_id,
    )
    row = (await session.execute(stmt)).one()
    d = _row_to_dict(row)
    return None if d["request_count"] == 0 else d
```

### 2. `usage_summary`（按 model+subject+project 分组，full 16 metric）

```python
async def usage_summary(session, *, start=None, end=None, model=None,
                        subject_id=None, project_id=None, limit=None) -> list[dict]:
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
```

### 3. `usage_ranking`（按 subject 分组，core 6 metric，JOIN subjects）

```python
async def usage_ranking(session, *, start=None, end=None, model=None,
                        limit=20) -> list[dict]:
    """按 subject 分组排名。core 6 metric（不含 cached_tokens/延迟/vllm），JOIN subjects。
    固定过滤 subject_id IS NOT NULL（对齐 DuckDB）。"""
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
```

**关键**：ranking 用 `_core_metric_columns()`（6 个），不是 full（18 个）——精确对齐 DuckDB。core 不含 cached_tokens。

### 4. `time_buckets`（按时间桶分组，full 16 metric）

```python
async def time_buckets(session, *, bucket="hour", start=None, end=None,
                       model=None, subject_id=None, project_id=None) -> list[dict]:
    if bucket not in _VALID_BUCKETS:
        raise ValueError(f"Invalid bucket: {bucket!r}")
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
```

### 5. `drilldown`（按维度分组，full 16 metric）

```python
async def drilldown(session, *, dimension="model", start=None, end=None,
                    model=None, subject_id=None, project_id=None, limit=100) -> list[dict]:
    dim_selects, join_target, group_by = _dimension_columns(dimension)
    stmt = _apply_filters(
        select(*dim_selects, *_full_metric_columns()).select_from(RequestFact),
        start=start, end=end, model=model, subject_id=subject_id, project_id=project_id,
    )
    if join_target is not None:
        if join_target is Subject:
            stmt = stmt.outerjoin(Subject, RequestFact.subject_id == Subject.id)
        else:  # Project
            stmt = stmt.outerjoin(Project, RequestFact.project_id == Project.id)
    stmt = stmt.group_by(*group_by).order_by(desc(text("request_count"))).limit(int(limit))
    rows = (await session.execute(stmt)).all()
    result = [_row_to_dict(row) for row in rows]
    for row in result:
        if row.get("dimension_id") is not None:
            row["dimension_id"] = str(row["dimension_id"])
    return result
```

## `_row_to_dict` 辅助

```python
def _row_to_dict(row) -> dict:
    """把 SQLAlchemy Row 转 dict，键用 label 名。UUID/datetime 序列化对齐 DuckDB。"""
    d = {}
    for key in row._mapping.keys():
        d[key] = row._mapping[key]
    return d
```

DuckDB 的 `_serialize_value` 把 UUID 转 str、datetime 转 ISO——Postgres 版在函数末尾按需转换（ranking/drilldown 的 dimension_id 转 str、time_buckets 的 bucket_start 转 ISO），其余字段保持原样（数值/字符串）。

## admin 端点改造（`api/admin/observability.py`）

5 个端点统一改造模式：

```python
# 改造前
from llm_gateway.services.duckdb_analytics import get_analytics

@router.get("/usage/totals")
async def usage_totals(start=None, end=None, model=None, subject_id=None, project_id=None):
    return await get_analytics().usage_totals(start=start, end=end, model=model,
                                              subject_id=subject_id, project_id=project_id)

# 改造后
from llm_gateway.api.deps import session_dep
from llm_gateway.services import analytics

@router.get("/usage/totals")
async def usage_totals(
    start=None, end=None, model=None, subject_id=None, project_id=None,
    session: AsyncSession = Depends(session_dep),
):
    return await analytics.usage_totals(
        session, start=start, end=end, model=model,
        subject_id=subject_id, project_id=project_id,
    )
```

5 个端点（usage_totals / usage_summary / usage_ranking / analytics_time_buckets / analytics_drilldown）都加 `session: AsyncSession = Depends(session_dep)`。`audit-events` 端点已用 session_dep，不动。

## auth.py / main.py 改动

**auth.py**：删除 line 32 `from llm_gateway.services.duckdb_analytics import get_analytics`（未使用）。

**main.py**：
- 删除 `from llm_gateway.services.duckdb_analytics import close_analytics, init_analytics`
- 删除 startup 的 `init_analytics(settings)`
- 删除 shutdown 的 `close_analytics()`

shutdown 顺序变为：`health_checker.stop()` → `drain_now()` → 完成（analytics 无状态可关）。

## 删除项

| 删除项 | 说明 |
|---|---|
| `src/llm_gateway/services/duckdb_analytics.py` | 整个文件（含连接管理、postgres 扩展加载、5 个查询方法、SQL 拼接辅助） |
| `pyproject.toml` 的 `duckdb==1.5.3` | 依赖（删后 `uv lock` 更新锁文件） |
| `vendor/duckdb/` | 离线 postgres 扩展包（`vendor/` 下仅此一个子目录，删除整个 `vendor/duckdb`） |

运行时缓存 `~/.duckdb/extensions/` 不在 git，无需处理（卸载 duckdb 后自然失效）。

## 测试策略

### 新增 `tests/test_analytics.py`

针对 `analytics.py` 的 5 个查询函数直接测试（传 AsyncSession，不经 HTTP）。用 `test_managed_usage_ranking.py` 同样的 `_seed_request_fact` 范式造数据。

| 测试 | 验证点 |
|---|---|
| `test_usage_totals_aggregates_all_metrics` | 18 个 metric 字段都在、值正确、无数据返回 None |
| `test_usage_totals_filters` | start/end/model/subject_id/project_id 各自命中正确数据 |
| `test_usage_summary_groups_by_model_subject_project` | 分组正确、排序 total_tokens DESC |
| `test_usage_summary_respects_limit` | limit 截断 |
| `test_usage_ranking_groups_by_subject_core_metrics` | **只返回 6 个 core metric**（关键：不含 cached_tokens/avg_latency 等） |
| `test_usage_ranking_excludes_null_subject` | subject_id 为 NULL 不进排名 |
| `test_time_buckets_groups_by_hour` | date_trunc 桶正确、bucket_start 转 ISO、排序 DESC |
| `test_time_buckets_invalid_bucket_raises` | 非法 bucket 抛 ValueError |
| `test_drilldown_by_model` | model 维度分组 |
| `test_drilldown_by_subject_joins_subjects` | subject 维度 JOIN + dimension_id 转 str |
| `test_drilldown_by_project_joins_projects` | project 维度 JOIN |
| `test_drilldown_by_endpoint_outcome_streaming` | 自身列维度无 JOIN |

### 等价性验证

**按 DuckDB 现有行为写断言作为黄金参照。** 实现时逐字段核对 DuckDB SQL，确保 Postgres 版字段名/类型/排序/边界完全一致。重点核对：
- ranking 用 core 6 metric（不是 full 18）—— 这是最易错的点
- usage_totals 无数据返回 None（不是空 dict）
- time_buckets 的 bucket_start 是 ISO 字符串（不是 datetime 对象）
- drilldown 的 dimension_id 是 str（不是 UUID）
- 所有 COALESCE 默认值对齐（0 / '无用户' / '无项目' / '无模型'）

### 回归

- 现有 admin observability 相关测试（test_backend_integration 等）全量跑，验证端点行为不变
- auth.py 的 `_usage_*_from_postgres` 测试不受影响（保持原位）

### 索引清理验证

- migration `0010` upgrade 后，用 `pg_indexes` 查询确认 `request_facts` 仅剩 6 个索引（pkey + request_id + started_at + model_started + subject_started + project_started）
- migration `0010` downgrade 后，16 个索引重建
- 跑一遍 analytics 的 12 个测试，确认删索引后查询结果和性能仍正常（聚合查询不依赖这些索引，删了不影响正确性，只影响某些全表场景的计划选择——但保留的 6 个已覆盖所有实际过滤组合）

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `src/llm_gateway/services/analytics.py` | **新增**：5 个查询函数 + 共享辅助 |
| `src/llm_gateway/services/duckdb_analytics.py` | **删除** |
| `src/llm_gateway/api/admin/observability.py` | 5 端点改调 analytics + 加 session_dep |
| `src/llm_gateway/api/auth.py` | 删除未使用的 get_analytics import |
| `src/llm_gateway/main.py` | 删除 init/close_analytics 生命周期 |
| `pyproject.toml` | 删除 duckdb 依赖 |
| `uv.lock` | uv lock 更新 |
| `vendor/duckdb/` | **删除整个目录** |
| `alembic/versions/20260629_0010_drop_unused_request_fact_indexes.py` | **新增**：drop 16 个冗余索引（含对称 downgrade） |
| `tests/test_analytics.py` | **新增**：12 个测试 |
| 前端 | **无改动** |
