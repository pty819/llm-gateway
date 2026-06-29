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
            outcome=RequestOutcome.SUCCESS if outcome == "success" else RequestOutcome.UPSTREAM_FAILURE,
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

    # 用大 token 数确保 alice 排进 top N（跨测试 DB 有大量历史数据）
    await _seed_request_fact(subject_id=alice_id, project_id=project_id, total_tokens=999999)

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
    assert alice_row["total_tokens"] == 999999
    assert alice_row["subject_name"] == "RankAlice"
    assert "login_username" in alice_row


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


async def test_drilldown_by_outcome():
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.analytics import drilldown

    async with AsyncSessionLocal() as session:
        project = Project(name=f"dd-out-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        subject = Subject(name="DdOutUser", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        await session.commit()
        project_id = project.id
        subject_id = subject.id

    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=100)
    await _seed_request_fact(subject_id=subject_id, project_id=project_id, total_tokens=50, outcome="upstream_failure")

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
    assert "upstream_failure" in labels
