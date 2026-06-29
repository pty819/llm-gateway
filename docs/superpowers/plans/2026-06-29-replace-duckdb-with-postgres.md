# 用 Postgres 替换 DuckDB Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 admin observability 的 5 个用量查询端点从 DuckDB 迁移到 Postgres 直查（行为完全等价，前端零改动），彻底删除 DuckDB 服务文件/依赖/vendor 扩展/生命周期，并清理 request_facts 表上 16 个当前查询模式下无用的索引。

**Architecture:** 新建 `services/analytics.py` 提供 5 个模块级 async 查询函数（SQLAlchemy ORM 风格，对齐现有 `_usage_summary_from_postgres` 范式），admin 端点改调它并加 `session_dep`。ranking 用 core 6 metric，其余 4 个用 full 18 metric——精确对齐 DuckDB 现有字段和顺序。索引清理通过新 alembic migration `0010` 完成。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy 2 async、SQLModel、alembic、pytest（pytest-asyncio）、uv（依赖/锁文件）。

参考 spec：`docs/superpowers/specs/2026-06-29-replace-duckdb-with-postgres-design.md`

---

## File Structure

| 文件 | 责任 | 操作 |
|---|---|---|
| `src/llm_gateway/services/analytics.py` | 5 个 Postgres 用量查询函数 + 共享辅助（metric 列、过滤、维度映射） | 新增 |
| `src/llm_gateway/api/admin/observability.py` | 5 端点改调 analytics + 加 session_dep | 修改 |
| `src/llm_gateway/services/duckdb_analytics.py` | DuckDB 服务（删除） | 删除 |
| `src/llm_gateway/api/auth.py` | 删未使用的 get_analytics import | 修改 |
| `src/llm_gateway/main.py` | 删 init/close_analytics 生命周期 | 修改 |
| `pyproject.toml` + `uv.lock` | 删 duckdb 依赖 | 修改 |
| `vendor/duckdb/` | 离线 postgres 扩展 | 删除 |
| `alembic/versions/20260629_0010_drop_unused_request_fact_indexes.py` | drop 16 索引 | 新增 |
| `tests/test_analytics.py` | 5 个查询函数的测试 | 新增 |

---

## Task 1: 新建 `analytics.py` — 共享辅助 + `usage_totals`（TDD）

**Files:**
- Create: `src/llm_gateway/services/analytics.py`
- Test: `tests/test_analytics.py`

- [ ] **Step 1: 写失败测试 `tests/test_analytics.py`**

```python
from __future__ import annotations

from datetime import timedelta

import pytest


pytestmark = pytest.mark.asyncio(loop_scope="session")


_FULL_METRIC_KEYS = {
    "request_count", "prompt_tokens", "completion_tokens", "total_tokens",
    "cached_tokens", "success_count", "failure_count",
    "avg_latency_ms", "avg_ttft_ms", "avg_stream_duration_ms",
    "retry_count", "fallback_count", "fallback_tokens",
    "avg_queue_ms", "avg_prefill_ms", "avg_decode_ms", "avg_kv_cache_usage",
    "vllm_metrics_count",
}


async def _seed_request_fact(*, subject_id, project_id=None, model_alias="test-model",
                             total_tokens=100, prompt_tokens=10, completion_tokens=None,
                             outcome="success"):
    """Insert a minimal RequestFact row for aggregation tests."""
    from llm_gateway.db.models import EndpointFamily, RequestOutcome, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.facts import record_request_fact

    if completion_tokens is None:
        completion_tokens = total_tokens - prompt_tokens
    now = utcnow()
    async with AsyncSessionLocal() as session:
        await record_request_fact(
            session,
            request_id=f"req-{subject_id}-{project_id}-{now.isoformat()}-{total_tokens}",
            started_at=now,
            ended_at=now,
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            subject_id=subject_id,
            subject_type="user",
            project_id=project_id,
            model_alias=model_alias,
            upstream_target_id=None,
            streaming=False,
            outcome=RequestOutcome.SUCCESS if outcome == "success" else RequestOutcome.ERROR,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        )
        await session.commit()


async def test_usage_totals_aggregates_all_metrics():
    """usage_totals 返回 18 个 metric，值正确。"""
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import usage_totals

    async with AsyncSessionLocal() as session:
        project = Project(name=f"totals-test-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        subject = Subject(name="TotalsUser", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        await session.commit()
        project_id = project.id
        subject_id = subject.id

    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=500)
    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=300)

    now = utcnow()
    async with AsyncSessionLocal() as session:
        result = await usage_totals(
            session, start=now - timedelta(days=1), end=now + timedelta(hours=1),
            project_id=project_id,
        )

    assert result is not None
    assert set(result.keys()) == _FULL_METRIC_KEYS
    assert result["request_count"] == 2
    assert result["total_tokens"] == 800
    assert result["prompt_tokens"] == 20
    assert result["completion_tokens"] == 780


async def test_usage_totals_returns_none_when_no_data():
    from uuid import uuid4

    from llm_gateway.db.models import utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import usage_totals

    now = utcnow()
    async with AsyncSessionLocal() as session:
        result = await usage_totals(
            session, start=now - timedelta(days=1), end=now + timedelta(hours=1),
            project_id=uuid4(),
        )
    assert result is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_analytics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_gateway.services.analytics'`

- [ ] **Step 3: 实现 `analytics.py`（共享辅助 + usage_totals）**

创建 `src/llm_gateway/services/analytics.py`：

```python
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import case, desc, func, select, text
from sqlmodel import col

from llm_gateway.db.models import Project, RequestFact, Subject


_SUCCESS_CASE = case(
    (func.lower(col(RequestFact.outcome)) == "success", 1), else_=0
)

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
            (func.lower(col(RequestFact.outcome)) != "success", 1), else_=0
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


def _row_to_dict(row) -> dict:
    """把 SQLAlchemy Row 转 dict，键用 label 名。"""
    return dict(row._mapping)


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
    stmt = _apply_filters(
        select(*_full_metric_columns()).select_from(RequestFact),
        start=start, end=end, model=model, subject_id=subject_id, project_id=project_id,
    )
    row = (await session.execute(stmt)).one()
    d = _row_to_dict(row)
    return None if d["request_count"] == 0 else d
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_analytics.py -v`
Expected: PASS（2 个测试）

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add src/llm_gateway/services/analytics.py tests/test_analytics.py && git commit -m "Add analytics.usage_totals with shared metric/filter helpers"
```

---

## Task 2: `usage_summary` + `usage_ranking`（TDD）

**Files:**
- Modify: `src/llm_gateway/services/analytics.py`（追加 2 个函数）
- Test: `tests/test_analytics.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_analytics.py` 末尾追加：

```python
async def test_usage_summary_groups_by_model_subject_project():
    """usage_summary 按 (model, subject, project) 分组，full 18 metric，total_tokens DESC。"""
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import usage_summary

    async with AsyncSessionLocal() as session:
        project = Project(name=f"summary-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        alice = Subject(name="SummaryAlice", type=SubjectType.USER)
        bob = Subject(name="SummaryBob", type=SubjectType.USER)
        session.add_all([alice, bob])
        await session.flush()
        await session.commit()
        project_id = project.id
        alice_id = alice.id
        bob_id = bob.id

    await _seed_request_fact(subject_id=alice_id, project_id=project_id, model_alias="m1", total_tokens=500)
    await _seed_request_fact(subject_id=alice_id, project_id=project_id, model_alias="m2", total_tokens=100)
    await _seed_request_fact(subject_id=bob_id, project_id=project_id, model_alias="m1", total_tokens=300)

    now = utcnow()
    async with AsyncSessionLocal() as session:
        rows = await usage_summary(
            session, start=now - timedelta(days=1), end=now + timedelta(hours=1),
            project_id=project_id,
        )

    # 按 total_tokens DESC：alice/m1(500) > bob/m1(300) > alice/m2(100)
    assert len(rows) == 3
    assert rows[0]["model_alias"] == "m1"
    assert rows[0]["subject_id"] == alice_id
    assert rows[0]["total_tokens"] == 500
    assert rows[1]["subject_id"] == bob_id
    assert rows[2]["model_alias"] == "m2"
    # 含全部 18 个 metric
    assert _FULL_METRIC_KEYS <= set(rows[0].keys())


async def test_usage_summary_respects_limit():
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import usage_summary

    async with AsyncSessionLocal() as session:
        project = Project(name=f"sumlimit-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        subject = Subject(name="SumLimitUser", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        await session.commit()
        project_id = project.id
        subject_id = subject.id

    for i in range(5):
        await _seed_request_fact(subject_id=subject_id, project_id=project_id, model_alias=f"m{i}", total_tokens=100 * (i + 1))

    now = utcnow()
    async with AsyncSessionLocal() as session:
        rows = await usage_summary(
            session, start=now - timedelta(days=1), end=now + timedelta(hours=1),
            project_id=project_id, limit=2,
        )
    assert len(rows) == 2


async def test_usage_ranking_uses_core_metrics_excludes_cached():
    """ranking 用 core 6 metric（不含 cached_tokens/avg_*）。关键等价性测试。"""
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import usage_ranking

    async with AsyncSessionLocal() as session:
        project = Project(name=f"rank-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        alice = Subject(name="RankAlice", type=SubjectType.USER)
        session.add(alice)
        await session.flush()
        await session.commit()
        alice_id = alice.id
        project_id = project.id

    await _seed_request_fact(subject_id=alice_id, project_id=project_id, total_tokens=500)

    now = utcnow()
    async with AsyncSessionLocal() as session:
        rows = await usage_ranking(
            session, start=now - timedelta(days=1), end=now + timedelta(hours=1),
            limit=20,
        )

    alice_row = next(r for r in rows if r["subject_id"] == alice_id)
    # core 6 metric，不含 cached_tokens/avg_*
    assert "cached_tokens" not in alice_row
    assert "avg_latency_ms" not in alice_row
    assert "vllm_metrics_count" not in alice_row
    assert alice_row["total_tokens"] == 500
    assert alice_row["subject_name"] == "RankAlice"
    assert "login_username" in alice_row
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_analytics.py -k "usage_summary or usage_ranking" -v`
Expected: FAIL — `ImportError: cannot import name 'usage_summary'`

- [ ] **Step 3: 追加 `usage_summary` 和 `usage_ranking`**

在 `src/llm_gateway/services/analytics.py` 的 `usage_totals` 之后追加：

```python
async def usage_summary(session, *, start=None, end=None, model=None,
                        subject_id=None, project_id=None, limit=None) -> list[dict]:
    """按 (model_alias, subject_id, project_id) 分组，full 18-metric。
    排序 total_tokens DESC, request_count DESC。limit 可选（None=不限制）。"""
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

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_analytics.py -k "usage_summary or usage_ranking" -v`
Expected: PASS（3 个测试）

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add src/llm_gateway/services/analytics.py tests/test_analytics.py && git commit -m "Add analytics.usage_summary and usage_ranking"
```

---

## Task 3: `time_buckets` + `drilldown`（TDD）

**Files:**
- Modify: `src/llm_gateway/services/analytics.py`（追加 `_dimension_columns` + 2 个函数）
- Test: `tests/test_analytics.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_analytics.py` 末尾追加：

```python
async def test_time_buckets_groups_by_hour_and_returns_iso():
    """time_buckets 按 date_trunc 桶分组，full 18 metric，bucket_start 是 ISO 字符串。"""
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import time_buckets

    async with AsyncSessionLocal() as session:
        project = Project(name=f"bucket-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        subject = Subject(name="BucketUser", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        await session.commit()
        project_id = project.id
        subject_id = subject.id

    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=100)
    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=200)

    now = utcnow()
    async with AsyncSessionLocal() as session:
        rows = await time_buckets(
            session, bucket="hour", start=now - timedelta(days=1), end=now + timedelta(hours=1),
            project_id=project_id,
        )

    assert len(rows) >= 1
    bucket = rows[0]
    # bucket_start 是字符串（ISO），不是 datetime 对象
    assert isinstance(bucket["bucket_start"], str)
    assert "+" in bucket["bucket_start"] or bucket["bucket_start"].endswith("Z")
    assert bucket["total_tokens"] == 300
    assert _FULL_METRIC_KEYS <= set(bucket.keys())


async def test_time_buckets_invalid_bucket_raises():
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import time_buckets

    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError):
            await time_buckets(session, bucket="fortnight")


async def test_drilldown_by_model():
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import drilldown

    async with AsyncSessionLocal() as session:
        project = Project(name=f"dd-model-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        subject = Subject(name="DdModelUser", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        await session.commit()
        project_id = project.id
        subject_id = subject.id

    await _seed_request_fact(subject_id=subject_id, project_id=project_id, model_alias="m1", total_tokens=100)
    await _seed_request_fact(subject_id=subject_id, project_id=project_id, model_alias="m2", total_tokens=200)

    now = utcnow()
    async with AsyncSessionLocal() as session:
        rows = await drilldown(
            session, dimension="model",
            start=now - timedelta(days=1), end=now + timedelta(hours=1),
            project_id=project_id,
        )

    assert len(rows) == 2
    # 按 request_count DESC
    assert {"dimension_id", "dimension_label"} <= set(rows[0].keys())
    labels = {r["dimension_label"] for r in rows}
    assert labels == {"m1", "m2"}
    assert _FULL_METRIC_KEYS <= set(rows[0].keys())


async def test_drilldown_by_subject_joins_subjects_and_str_id():
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import drilldown

    async with AsyncSessionLocal() as session:
        project = Project(name=f"dd-subj-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        subject = Subject(name="DdSubjUser", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        await session.commit()
        project_id = project.id
        subject_id = subject.id

    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=100)

    now = utcnow()
    async with AsyncSessionLocal() as session:
        rows = await drilldown(
            session, dimension="subject",
            start=now - timedelta(days=1), end=now + timedelta(hours=1),
            project_id=project_id,
        )

    assert len(rows) >= 1
    row = next(r for r in rows if r["dimension_label"] == "DdSubjUser")
    # dimension_id 是 str（对齐 DuckDB），不是 UUID
    assert isinstance(row["dimension_id"], str)


async def test_drilldown_by_project_joins_projects():
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import drilldown

    async with AsyncSessionLocal() as session:
        project = Project(name=f"dd-proj-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        subject = Subject(name="DdProjUser", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        await session.commit()
        project_id = project.id
        subject_id = subject.id

    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=100)

    now = utcnow()
    async with AsyncSessionLocal() as session:
        rows = await drilldown(
            session, dimension="project",
            start=now - timedelta(days=1), end=now + timedelta(hours=1),
            project_id=project_id,
        )

    assert len(rows) >= 1
    assert any(r["dimension_label"] == project.name for r in rows)


async def test_drilldown_by_endpoint_outcome_streaming():
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import drilldown

    async with AsyncSessionLocal() as session:
        project = Project(name=f"dd-self-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        subject = Subject(name="DdSelfUser", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        await session.commit()
        project_id = project.id
        subject_id = subject.id

    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=100)
    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=50, outcome="error")

    now = utcnow()
    async with AsyncSessionLocal() as session:
        rows = await drilldown(
            session, dimension="outcome",
            start=now - timedelta(days=1), end=now + timedelta(hours=1),
            project_id=project_id,
        )

    labels = {r["dimension_label"] for r in rows}
    # RequestOutcome enum 值
    assert "success" in labels
    assert "error" in labels
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_analytics.py -k "time_buckets or drilldown" -v`
Expected: FAIL — `ImportError: cannot import name 'time_buckets'`

- [ ] **Step 3: 追加 `_dimension_columns` + `time_buckets` + `drilldown`**

在 `src/llm_gateway/services/analytics.py` 的 `usage_ranking` 之后追加：

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_analytics.py -v`
Expected: PASS（全部 11 个测试）

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add src/llm_gateway/services/analytics.py tests/test_analytics.py && git commit -m "Add analytics.time_buckets and drilldown with dimension mapping"
```

---

## Task 4: admin 端点切换到 analytics（含 import 清理）

**Files:**
- Modify: `src/llm_gateway/api/admin/observability.py`（5 端点 + import）
- Modify: `src/llm_gateway/api/auth.py`（删 line 32 import）
- Modify: `src/llm_gateway/main.py`（删 init/close_analytics）

**无新增测试**——端点是薄包装，行为由 Task 1-3 的 analytics 测试覆盖。回归靠全量测试套件。

- [ ] **Step 1: 改 observability.py 的 import**

在 `src/llm_gateway/api/admin/observability.py`：

(a) 把 line 12：
```python
from llm_gateway.services.duckdb_analytics import get_analytics
```
替换为：
```python
from llm_gateway.services import analytics
```

- [ ] **Step 2: 改 5 个端点**

把 observability.py 的 5 个查询端点（usage_summary / usage_totals / usage_ranking / analytics_time_buckets / analytics_drilldown）全部改造。每个端点：加 `session: AsyncSession = Depends(session_dep)` 参数，把 `await get_analytics().xxx(...)` 改为 `await analytics.xxx(session, ...)`。

具体地，把以下 5 个函数体替换（保留装饰器和参数签名，只加 session 参数 + 改调用）。逐一替换：

**usage_summary（line ~24）：**
```python
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
        start=start, end=end, model=model,
        subject_id=subject_id, project_id=project_id, limit=limit,
    )
```

**usage_totals（line ~43）：**
```python
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
        start=start, end=end, model=model,
        subject_id=subject_id, project_id=project_id,
    )
```

**usage_ranking（line ~60）：**
```python
@router.get("/usage/ranking")
async def usage_ranking(
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(session_dep),
):
    return await analytics.usage_ranking(
        session, start=start, end=end, model=model, limit=limit,
    )
```

**analytics_time_buckets（line ~75）：**
```python
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
        session, bucket=bucket, start=start, end=end,
        model=model, subject_id=subject_id, project_id=project_id,
    )
```

**analytics_drilldown（line ~94）：**
```python
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
        session, dimension=dimension, start=start, end=end,
        model=model, subject_id=subject_id, project_id=project_id, limit=limit,
    )
```

- [ ] **Step 3: 删 auth.py 的未使用 import**

在 `src/llm_gateway/api/auth.py`，删除 line 32：
```python
from llm_gateway.services.duckdb_analytics import get_analytics
```

- [ ] **Step 4: 改 main.py 生命周期**

在 `src/llm_gateway/main.py`：

(a) 删除 import 行（约 line 9）：
```python
from llm_gateway.services.duckdb_analytics import close_analytics, init_analytics
```

(b) 在 lifespan 里删除 startup 的 `init_analytics(settings)`（约 line 50）。

(c) 在 lifespan 里删除 shutdown 的 `close_analytics()`（约 line 57）。

改造后 lifespan 片段应为：
```python
        litellm.request_timeout = settings.upstream_timeout_seconds
        await health_checker.start()
        yield
        await health_checker.stop()
        await drain_now()
```

- [ ] **Step 5: 验证 import 无误 + app 能起**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -c "import llm_gateway.main; import llm_gateway.api.admin.observability; import llm_gateway.api.auth; print('imports OK')"`
Expected: `imports OK`（无 NameError/ImportError）

- [ ] **Step 6: 跑现有 admin/observability 相关测试确认无回归**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/ -k "usage or analytics or observability or managed" -v 2>&1 | tail -20`
Expected: 现有测试全过（端点行为不变）

- [ ] **Step 7: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add src/llm_gateway/api/admin/observability.py src/llm_gateway/api/auth.py src/llm_gateway/main.py && git commit -m "Switch admin observability endpoints to Postgres analytics; remove DuckDB wiring"
```

---

## Task 5: 删除 DuckDB 服务文件、依赖、vendor 扩展

**Files:**
- Delete: `src/llm_gateway/services/duckdb_analytics.py`
- Delete: `vendor/duckdb/`（整个目录）
- Modify: `pyproject.toml`、`uv.lock`

- [ ] **Step 1: 确认无残留引用**

Run: `cd /Users/liyifan/llm_gateway && grep -rn "duckdb_analytics\|init_analytics\|close_analytics\|get_analytics\|DuckDBAnalytics\|from llm_gateway.services.duckdb" src/ tests/ 2>/dev/null | grep -v __pycache__ | grep -v "\.pyc"`
Expected: 无输出（所有引用已在 Task 4 清除）。若有残留，先清理再继续。

- [ ] **Step 2: 删除 duckdb_analytics.py 和 vendor/duckdb**

```bash
cd /Users/liyifan/llm_gateway && rm src/llm_gateway/services/duckdb_analytics.py && rm -rf vendor/duckdb && ls vendor/ 2>/dev/null && rmdir vendor 2>/dev/null; echo "done"
```

注意：若 `vendor/` 删空了（`ls` 无输出），用 `rmdir vendor` 删除空目录。`echo "done"` 确认执行完成。

- [ ] **Step 3: 从 pyproject.toml 删除 duckdb 依赖**

在 `pyproject.toml`，删除这一行（在 dependencies 列表里）：
```toml
    "duckdb==1.5.3",
```

- [ ] **Step 4: 更新 uv.lock**

Run: `cd /Users/liyifan/llm_gateway && uv lock 2>&1 | tail -5`
Expected: lock 文件更新成功，duckdb 相关条目被移除。

- [ ] **Step 5: 验证依赖同步 + import 仍正常**

Run: `cd /Users/liyifan/llm_gateway && uv sync --quiet 2>&1 | tail -3 && .venv/bin/python -c "import duckdb" 2>&1 | tail -1`
Expected: `uv sync` 成功；`import duckdb` 应报 `ModuleNotFoundError`（确认已彻底移除）。

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -c "import llm_gateway.main; print('app imports OK')"`
Expected: `app imports OK`（移除 duckdb 后 app 仍能正常 import）

- [ ] **Step 6: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add -A && git commit -m "Remove DuckDB service, dependency, and vendor extensions"
```

---

## Task 6: 新建 migration `0010` 删除 16 个冗余索引

**Files:**
- Create: `alembic/versions/20260629_0010_drop_unused_request_fact_indexes.py`

- [ ] **Step 1: 确认当前 head migration**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/alembic heads 2>&1 | tail -2`
Expected: `20260615_0009 (... )`（确认 0009 是当前 head）

- [ ] **Step 2: 创建 migration 文件**

创建 `alembic/versions/20260629_0010_drop_unused_request_fact_indexes.py`：

```python
"""Drop unused request_facts indexes.

Revision ID: 20260629_0010
Revises: 20260615_0009
Create Date: 2026-06-29

These 16 indexes are either covered by composite indexes
(model_started/subject_started/project_started cover the single-column
model_alias/subject_id/project_id prefixes) or never participate in any
query filter/group under the current Postgres-direct analytics path. Dropping
them reduces per-insert write cost. downgrade recreates them all.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260629_0010"
down_revision: str | None = "20260615_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (index_name, columns, extra_kwargs) —— extra_kwargs 用于 BRIN 索引重建
_DROPPED_INDEXES = [
    ("ix_request_facts_started_model", ["started_at", "model_alias"], {}),
    ("ix_request_facts_started_subject", ["started_at", "subject_id"], {}),
    ("ix_request_facts_started_project", ["started_at", "project_id"], {}),
    ("ix_request_facts_started_request", ["started_at", "request_id"], {}),
    ("ix_request_facts_started_at_brin", ["started_at"], {"postgresql_using": "brin"}),
    ("ix_request_facts_subject_id", ["subject_id"], {}),
    ("ix_request_facts_project_id", ["project_id"], {}),
    ("ix_request_facts_model_alias", ["model_alias"], {}),
    ("ix_request_facts_ended_at", ["ended_at"], {}),
    ("ix_request_facts_usage_source", ["usage_source"], {}),
    ("ix_request_facts_outcome", ["outcome"], {}),
    ("ix_request_facts_endpoint_family", ["endpoint_family"], {}),
    ("ix_request_facts_streaming", ["streaming"], {}),
    ("ix_request_facts_error_class", ["error_class"], {}),
    ("ix_request_facts_subject_type", ["subject_type"], {}),
    ("ix_request_facts_upstream_target_id", ["upstream_target_id"], {}),
]


def upgrade() -> None:
    for index_name, _columns, _kwargs in _DROPPED_INDEXES:
        op.drop_index(index_name, table_name="request_facts", if_exists=True)


def downgrade() -> None:
    for index_name, columns, kwargs in reversed(_DROPPED_INDEXES):
        op.create_index(
            index_name, "request_facts", columns, if_not_exists=True, **kwargs
        )
```

- [ ] **Step 3: 应用 migration 验证 upgrade**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/alembic upgrade head 2>&1 | tail -5`
Expected: `Running upgrade 20260615_0009 -> 20260629_0010, ...`

- [ ] **Step 4: 验证只剩 6 个索引**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -c "
import asyncio
from llm_gateway.db.session import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(\"SELECT indexname FROM pg_indexes WHERE tablename='request_facts' ORDER BY indexname\"))).all()
        names = [r[0] for r in rows]
        print('indexes:', len(names))
        for n in names: print(' ', n)
        expected = {'request_facts_pkey', 'ix_request_facts_request_id', 'ix_request_facts_started_at', 'ix_request_facts_model_started', 'ix_request_facts_subject_started', 'ix_request_facts_project_started'}
        assert set(names) == expected, f'mismatch: {set(names) ^ expected}'
        print('OK: exactly 6 indexes')
asyncio.run(check())
"`
Expected: 打印 6 个索引名 + `OK: exactly 6 indexes`

- [ ] **Step 5: 验证 downgrade 能重建**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/alembic downgrade -1 2>&1 | tail -3 && .venv/bin/alembic upgrade head 2>&1 | tail -3`
Expected: downgrade 成功，再 upgrade 回来成功（验证对称性）

- [ ] **Step 6: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add alembic/versions/20260629_0010_drop_unused_request_fact_indexes.py && git commit -m "Drop 16 unused request_facts indexes (22 -> 6) via migration 0010"
```

---

## Task 7: 全量回归

- [ ] **Step 1: 跑全量后端测试**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/ 2>&1 | tail -5`
Expected: 全绿。重点关注：
- `tests/test_analytics.py`（新增 11 个）
- `tests/test_managed_usage_ranking.py`、`tests/test_self_key_management.py`（前序功能不应回归）
- `tests/test_backend_integration.py`（admin observability 端点行为不变）

- [ ] **Step 2: 推送前自检提交链**

Run: `cd /Users/liyifan/llm_gateway && git log --oneline -8`
确认提交链清晰：analytics 共享辅助 → summary/ranking → buckets/drilldown → 端点切换 → 删 DuckDB → migration 0010。

---

## Self-Review Checklist（实施前已自查）

**Spec 覆盖：**
- ✅ 5 个查询函数（usage_totals/summary/ranking/time_buckets/drilldown）→ Task 1-3
- ✅ ranking 用 core 6 metric（不含 cached_tokens）→ Task 2 test_usage_ranking_uses_core_metrics_excludes_cached
- ✅ 其余 4 个用 full 18 metric（cached_tokens 在 success 前）→ Task 1 _full_metric_columns 顺序对齐 DuckDB
- ✅ admin 端点改调 analytics + 加 session_dep → Task 4
- ✅ 删 duckdb_analytics.py / pyproject duckdb / vendor/duckdb / main.py 生命周期 → Task 4 + Task 5
- ✅ migration 0010 删 16 索引 → Task 6
- ✅ auth.py 删未使用 import → Task 4 Step 3

**等价性关键点（测试覆盖）：**
- ranking 不含 cached_tokens/avg_*/vllm → test_usage_ranking_uses_core_metrics_excludes_cached
- usage_totals 无数据返回 None → test_usage_totals_returns_none_when_no_data
- time_buckets bucket_start 是 ISO 字符串 → test_time_buckets_groups_by_hour_and_returns_iso
- drilldown dimension_id 是 str → test_drilldown_by_subject_joins_subjects_and_str_id
- COALESCE 默认值（0 / 无用户 / 无项目 / 无模型）→ drilldown 各维度测试

**类型一致性：**
- `usage_totals(session, *, start, end, model, subject_id, project_id)` 在 Task 1 定义，Task 4 端点调用签名一致 ✓
- `_core_metric_columns()` / `_full_metric_columns()` / `_apply_filters()` / `_dimension_columns()` 在 Task 1/3 定义，后续函数引用一致 ✓
