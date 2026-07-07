from __future__ import annotations

from uuid import uuid4

import pytest
from conftest import fetch_request_fact

from llm_gateway.db.models import EndpointFamily, RequestOutcome

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _auth_headers(raw_key: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}", "x-request-id": request_id}


async def test_non_stream_concurrency_limit_returns_429_and_records_rate_limited(
    client, gateway_fixture
):
    from llm_gateway.db.models import RatePolicy
    from llm_gateway.db.session import AsyncSessionLocal

    request_id = f"pytest-concurrency-non-stream-{uuid4()}"
    async with AsyncSessionLocal() as session:
        session.add(
            RatePolicy(
                scope="key",
                scope_id=gateway_fixture.key_id,
                requests_per_minute=99,
                concurrency_limit=0,
            )
        )
        await session.commit()

    response = await client.post(
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [{"role": "user", "content": "should be concurrency limited"}],
            "max_tokens": 16,
        },
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "concurrency_exceeded"

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.OPENAI_CHAT
    assert fact.outcome == RequestOutcome.RATE_LIMITED
    assert fact.streaming is False
    assert fact.error_detail == "concurrency_exceeded"


async def test_stream_concurrency_limit_emits_sse_error_after_lazy_acquire(client, gateway_fixture):
    """With lazy acquire, the streaming generator acquires the concurrency slot
    as its first action — AFTER StreamingResponse has begun (200 sent). So a
    concurrency-exceeded condition can no longer become a 429; instead the
    client receives 200 + an SSE error frame. This is the accepted trade-off
    of lazy acquire, which eliminates the slot-leak construction window."""
    from llm_gateway.db.models import RatePolicy
    from llm_gateway.db.session import AsyncSessionLocal

    request_id = f"pytest-concurrency-stream-{uuid4()}"
    async with AsyncSessionLocal() as session:
        session.add(
            RatePolicy(
                scope="key",
                scope_id=gateway_fixture.key_id,
                requests_per_minute=99,
                concurrency_limit=0,
            )
        )
        await session.commit()

    response = await client.post(
        "/v1/responses",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "input": "should be concurrency limited after stream starts",
            "stream": True,
        },
    )

    # 200 is sent before the generator runs (StreamingResponse contract).
    assert response.status_code == 200
    body = response.text
    assert "event: error" in body
    assert "concurrency_exceeded" in body

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.OPENAI_RESPONSES
    assert fact.outcome == RequestOutcome.RATE_LIMITED
    assert fact.streaming is True
    assert fact.error_detail == "concurrency_exceeded"


async def test_model_entitlement_requires_exactly_one_existing_scope(client, gateway_fixture):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}

    too_many_scopes = await client.post(
        "/admin/model-entitlements",
        headers=headers,
        json={
            "model_alias_id": str(gateway_fixture.model_alias_id),
            "subject_id": str(gateway_fixture.subject_id),
            "project_id": str(gateway_fixture.project_id),
        },
    )
    assert too_many_scopes.status_code == 400
    assert too_many_scopes.json()["detail"] == "exactly_one_entitlement_scope_required"

    missing_subject = await client.post(
        "/admin/model-entitlements",
        headers=headers,
        json={
            "model_alias_id": str(gateway_fixture.model_alias_id),
            "subject_id": str(uuid4()),
        },
    )
    assert missing_subject.status_code == 404
    assert missing_subject.json()["detail"] == "Subject_not_found"
