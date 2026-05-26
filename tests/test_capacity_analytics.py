from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from llm_gateway.db.models import EndpointFamily, RequestFact, RequestOutcome, utcnow


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_admin_capacity_analytics_time_buckets_and_drilldown(
    client, gateway_fixture
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.session import AsyncSessionLocal

    now = utcnow()
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                RequestFact(
                    request_id=f"pytest-analytics-success-{uuid4()}",
                    started_at=now - timedelta(minutes=30),
                    ended_at=now - timedelta(minutes=29, seconds=59),
                    endpoint_family=EndpointFamily.OPENAI_RESPONSES,
                    subject_id=gateway_fixture.subject_id,
                    project_id=gateway_fixture.project_id,
                    model_alias=gateway_fixture.model_alias,
                    streaming=True,
                    outcome=RequestOutcome.SUCCESS,
                    prompt_tokens=100,
                    completion_tokens=40,
                    total_tokens=140,
                    cached_tokens=25,
                    latency_ms=1200,
                    time_to_first_token_ms=240,
                    stream_duration_ms=1180,
                    retry_count=1,
                    fallback_count=0,
                    queue_ms=30,
                    prefill_ms=300,
                    decode_ms=700,
                    kv_cache_usage=0.72,
                ),
                RequestFact(
                    request_id=f"pytest-analytics-failure-{uuid4()}",
                    started_at=now - timedelta(minutes=20),
                    ended_at=now - timedelta(minutes=19, seconds=59),
                    endpoint_family=EndpointFamily.OPENAI_CHAT,
                    subject_id=gateway_fixture.subject_id,
                    project_id=gateway_fixture.project_id,
                    model_alias=gateway_fixture.model_alias,
                    streaming=False,
                    outcome=RequestOutcome.ADAPTER_FAILURE,
                    prompt_tokens=20,
                    completion_tokens=0,
                    total_tokens=None,
                    latency_ms=500,
                    fallback_count=1,
                    fallback_tokens=20,
                ),
            ]
        )
        await session.commit()

    headers = {"x-admin-token": get_settings().admin_token}
    params = {
        "start": (now - timedelta(hours=1)).isoformat(),
        "end": (now + timedelta(hours=1)).isoformat(),
        "bucket": "hour",
        "model": gateway_fixture.model_alias,
    }
    buckets = await client.get(
        "/admin/analytics/time-buckets", headers=headers, params=params
    )
    assert buckets.status_code == 200, buckets.text
    bucket_payload = buckets.json()
    assert len(bucket_payload) >= 1
    row = bucket_payload[0]
    assert row["request_count"] >= 2
    assert row["total_tokens"] >= 160
    assert row["cached_tokens"] >= 25
    assert row["success_count"] >= 1
    assert row["failure_count"] >= 1
    assert row["avg_latency_ms"] is not None
    assert row["avg_ttft_ms"] is not None
    assert row["retry_count"] >= 1
    assert row["fallback_count"] >= 1
    assert row["vllm_metrics_count"] >= 1

    drilldown = await client.get(
        "/admin/analytics/drilldown",
        headers=headers,
        params={**params, "dimension": "model"},
    )
    assert drilldown.status_code == 200, drilldown.text
    drilldown_payload = drilldown.json()
    model_row = next(
        item
        for item in drilldown_payload
        if item["dimension_id"] == gateway_fixture.model_alias
    )
    assert model_row["dimension_label"] == gateway_fixture.model_alias
    assert model_row["request_count"] >= 2
