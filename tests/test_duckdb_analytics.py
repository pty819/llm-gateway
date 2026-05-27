from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from llm_gateway.db.models import EndpointFamily, RequestFact, RequestOutcome, utcnow


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_admin_duckdb_analytics_refresh_and_query(
    client, gateway_fixture, tmp_path
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.session import AsyncSessionLocal

    settings = get_settings()
    settings.analytics_duckdb_enabled = True
    settings.analytics_duckdb_path = str(tmp_path / "analytics.duckdb")

    now = utcnow()
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                RequestFact(
                    request_id=f"pytest-duckdb-success-{uuid4()}",
                    started_at=now - timedelta(minutes=45),
                    ended_at=now - timedelta(minutes=44, seconds=50),
                    endpoint_family=EndpointFamily.OPENAI_RESPONSES,
                    subject_id=gateway_fixture.subject_id,
                    project_id=gateway_fixture.project_id,
                    model_alias=gateway_fixture.model_alias,
                    streaming=True,
                    outcome=RequestOutcome.SUCCESS,
                    prompt_tokens=120,
                    completion_tokens=30,
                    total_tokens=150,
                    cached_tokens=50,
                    latency_ms=900,
                    time_to_first_token_ms=120,
                    stream_duration_ms=850,
                    retry_count=1,
                    fallback_count=0,
                    queue_ms=10,
                    prefill_ms=200,
                    decode_ms=500,
                    kv_cache_usage=0.64,
                ),
                RequestFact(
                    request_id=f"pytest-duckdb-failure-{uuid4()}",
                    started_at=now - timedelta(minutes=30),
                    ended_at=now - timedelta(minutes=29, seconds=55),
                    endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
                    subject_id=gateway_fixture.subject_id,
                    project_id=gateway_fixture.project_id,
                    model_alias=gateway_fixture.model_alias,
                    streaming=False,
                    outcome=RequestOutcome.UPSTREAM_FAILURE,
                    prompt_tokens=40,
                    completion_tokens=0,
                    total_tokens=None,
                    latency_ms=300,
                    fallback_count=1,
                    fallback_tokens=40,
                ),
            ]
        )
        await session.commit()

    headers = {"x-admin-token": settings.admin_token}
    params = {
        "start": (now - timedelta(hours=1)).isoformat(),
        "end": (now + timedelta(hours=1)).isoformat(),
        "bucket": "hour",
        "model": gateway_fixture.model_alias,
    }
    refresh = await client.post(
        "/admin/analytics/duckdb/refresh",
        headers=headers,
        json={"start": params["start"], "end": params["end"]},
    )
    assert refresh.status_code == 200, refresh.text
    refresh_payload = refresh.json()
    assert refresh_payload["rows_copied"] >= 2
    assert refresh_payload["row_count"] >= 2
    assert refresh_payload["file_size_bytes"] > 0

    buckets = await client.get(
        "/admin/analytics/duckdb/time-buckets", headers=headers, params=params
    )
    assert buckets.status_code == 200, buckets.text
    bucket_payload = buckets.json()
    assert bucket_payload
    row = _sum_rows(bucket_payload)
    assert row["request_count"] >= 2
    assert row["total_tokens"] >= 190
    assert row["cached_tokens"] >= 50
    assert row["success_count"] >= 1
    assert row["failure_count"] >= 1
    assert row["avg_latency_ms"] is not None
    assert row["avg_ttft_ms"] is not None
    assert row["retry_count"] >= 1
    assert row["fallback_count"] >= 1
    assert row["vllm_metrics_count"] >= 1

    drilldown = await client.get(
        "/admin/analytics/duckdb/drilldown",
        headers=headers,
        params={**params, "dimension": "model"},
    )
    assert drilldown.status_code == 200, drilldown.text
    model_row = next(
        item
        for item in drilldown.json()
        if item["dimension_id"] == gateway_fixture.model_alias
    )
    assert model_row["dimension_label"] == gateway_fixture.model_alias
    assert model_row["request_count"] >= 2


async def test_user_usage_stays_postgres_scoped(client, gateway_fixture):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import create_user_session

    now = utcnow()
    async with AsyncSessionLocal() as session:
        user_session, raw_session = await create_user_session(
            session, subject_id=gateway_fixture.subject_id, ttl_hours=1
        )
        session.add(
            RequestFact(
                request_id=f"pytest-user-usage-postgres-{uuid4()}",
                started_at=now - timedelta(minutes=5),
                ended_at=now - timedelta(minutes=4, seconds=55),
                endpoint_family=EndpointFamily.OPENAI_CHAT,
                subject_id=gateway_fixture.subject_id,
                project_id=gateway_fixture.project_id,
                model_alias=gateway_fixture.model_alias,
                streaming=False,
                outcome=RequestOutcome.SUCCESS,
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
            )
        )
        await session.commit()

    response = await client.get(
        "/auth/usage/summary",
        headers={"x-session-token": raw_session},
        params={
            "start": (now - timedelta(minutes=10)).isoformat(),
            "end": (now + timedelta(minutes=10)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["request_count"] >= 1
    assert payload["total_tokens"] >= 18
    assert user_session.subject_id == gateway_fixture.subject_id
    assert get_settings().analytics_duckdb_enabled is True


def _sum_rows(rows: list[dict]) -> dict:
    result = {
        "request_count": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "success_count": 0,
        "failure_count": 0,
        "retry_count": 0,
        "fallback_count": 0,
        "vllm_metrics_count": 0,
        "avg_latency_ms": None,
        "avg_ttft_ms": None,
    }
    for row in rows:
        for key in [
            "request_count",
            "total_tokens",
            "cached_tokens",
            "success_count",
            "failure_count",
            "retry_count",
            "fallback_count",
            "vllm_metrics_count",
        ]:
            result[key] += row[key]
        result["avg_latency_ms"] = result["avg_latency_ms"] or row["avg_latency_ms"]
        result["avg_ttft_ms"] = result["avg_ttft_ms"] or row["avg_ttft_ms"]
    return result
