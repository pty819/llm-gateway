from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from llm_gateway.db.models import (
    EndpointFamily,
    RequestFact,
    RequestOutcome,
    utcnow,
)
from tests.helpers import _employee_username

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_usage_ranking_falls_back_to_prompt_plus_completion_tokens_and_bounds_limit(
    client, gateway_fixture
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.session import AsyncSessionLocal

    request_id = f"pytest-ranking-{uuid4()}"
    async with AsyncSessionLocal() as session:
        session.add(
            RequestFact(
                request_id=request_id,
                started_at=utcnow(),
                ended_at=utcnow(),
                endpoint_family=EndpointFamily.OPENAI_CHAT,
                subject_id=gateway_fixture.subject_id,
                subject_type=None,
                project_id=gateway_fixture.project_id,
                model_alias=gateway_fixture.model_alias,
                upstream_target_id=None,
                streaming=False,
                outcome=RequestOutcome.SUCCESS,
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=None,
            )
        )
        await session.commit()

    headers = {"x-admin-token": get_settings().admin_token}
    ranking = await client.get(
        "/admin/usage/ranking",
        headers=headers,
        params={"model": gateway_fixture.model_alias, "limit": 1},
    )
    assert ranking.status_code == 200, ranking.text
    payload = ranking.json()
    assert payload[0]["subject_id"] == str(gateway_fixture.subject_id)
    assert payload[0]["total_tokens"] >= 18

    invalid_limit = await client.get("/admin/usage/ranking", headers=headers, params={"limit": 0})
    assert invalid_limit.status_code == 422


async def test_self_service_usage_summary_is_scoped_to_current_user(client):
    from llm_gateway.db.models import RequestFact, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal

    username = _employee_username()
    registered = await client.post(
        "/auth/register",
        json={
            "username": username,
            "full_name": "用量用户",
            "password": "correct-horse-battery",
        },
    )
    assert registered.status_code == 200, registered.text
    payload = registered.json()
    session_token = payload["session_token"]
    subject_id = payload["profile"]["subject"]["id"]
    project_id = payload["project"]["id"]

    other = await client.post(
        "/auth/register",
        json={
            "username": _employee_username(),
            "full_name": "其他用户",
            "password": "correct-horse-battery",
        },
    )
    assert other.status_code == 200, other.text

    now = utcnow()
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                RequestFact(
                    request_id=f"pytest-own-usage-success-{uuid4()}",
                    started_at=now - timedelta(minutes=10),
                    ended_at=now - timedelta(minutes=9),
                    endpoint_family=EndpointFamily.OPENAI_CHAT,
                    subject_id=subject_id,
                    subject_type=SubjectType.USER,
                    project_id=project_id,
                    model_alias="own-model",
                    upstream_target_id=None,
                    streaming=False,
                    outcome=RequestOutcome.SUCCESS,
                    prompt_tokens=11,
                    completion_tokens=7,
                    total_tokens=None,
                ),
                RequestFact(
                    request_id=f"pytest-own-usage-failure-{uuid4()}",
                    started_at=now - timedelta(minutes=5),
                    ended_at=now - timedelta(minutes=4),
                    endpoint_family=EndpointFamily.ANTHROPIC_MESSAGES,
                    subject_id=subject_id,
                    subject_type=SubjectType.USER,
                    project_id=project_id,
                    model_alias="own-model",
                    upstream_target_id=None,
                    streaming=True,
                    outcome=RequestOutcome.ADAPTER_FAILURE,
                    prompt_tokens=3,
                    completion_tokens=2,
                    total_tokens=20,
                ),
                RequestFact(
                    request_id=f"pytest-other-usage-{uuid4()}",
                    started_at=now - timedelta(minutes=5),
                    ended_at=now - timedelta(minutes=4),
                    endpoint_family=EndpointFamily.OPENAI_CHAT,
                    subject_id=other.json()["profile"]["subject"]["id"],
                    subject_type=SubjectType.USER,
                    project_id=other.json()["project"]["id"],
                    model_alias="other-model",
                    upstream_target_id=None,
                    streaming=False,
                    outcome=RequestOutcome.SUCCESS,
                    prompt_tokens=1000,
                    completion_tokens=1000,
                    total_tokens=2000,
                ),
            ]
        )
        await session.commit()

    response = await client.get(
        "/auth/usage/summary",
        headers={"x-session-token": session_token},
        params={
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["request_count"] == 2
    assert summary["prompt_tokens"] == 14
    assert summary["completion_tokens"] == 9
    assert summary["total_tokens"] == 38
    assert summary["success_count"] == 1
    assert summary["failure_count"] == 1
