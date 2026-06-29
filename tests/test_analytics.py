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
