from __future__ import annotations

from datetime import timedelta


import pytest


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_request_fact(*, project_id, subject_id, model_alias="test-model", total_tokens=100, outcome="success"):
    """Insert a minimal RequestFact row for aggregation tests."""
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import EndpointFamily, RequestOutcome, utcnow
    from llm_gateway.services.facts import record_request_fact

    now = utcnow()
    async with AsyncSessionLocal() as session:
        await record_request_fact(
            session,
            request_id=f"req-{project_id}-{subject_id}-{now.isoformat()}-{total_tokens}",
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
            usage={"prompt_tokens": 10, "completion_tokens": total_tokens - 10, "total_tokens": total_tokens},
        )
        await session.commit()


async def test_usage_ranking_groups_by_subject_and_sorts_by_total_tokens():
    """直接调用查询函数：两个 subject 在同一 project，按 total_tokens 降序。"""
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.api.auth import _usage_ranking_from_postgres

    async with AsyncSessionLocal() as session:
        project = Project(name=f"rank-test-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        alice = Subject(name="Alice", type=SubjectType.USER)
        bob = Subject(name="Bob", type=SubjectType.USER)
        session.add_all([alice, bob])
        await session.flush()
        await session.commit()
        project_id = project.id
        alice_id = alice.id
        bob_id = bob.id

    await _seed_request_fact(project_id=project_id, subject_id=alice_id, total_tokens=500)
    await _seed_request_fact(project_id=project_id, subject_id=alice_id, total_tokens=300)
    await _seed_request_fact(project_id=project_id, subject_id=bob_id, total_tokens=100)

    now = utcnow()
    start = now - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        ranking = await _usage_ranking_from_postgres(
            session, start=start, end=now + timedelta(hours=1), project_id=project_id, limit=20
        )

    assert len(ranking) == 2
    assert ranking[0]["subject_name"] == "Alice"
    assert ranking[0]["total_tokens"] == 800
    assert ranking[0]["request_count"] == 2
    assert ranking[1]["subject_name"] == "Bob"
    assert ranking[1]["total_tokens"] == 100
    assert ranking[1]["request_count"] == 1


async def test_usage_ranking_filters_by_model():
    """传 model 参数时只聚合该 model 的用量。"""
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.api.auth import _usage_ranking_from_postgres

    async with AsyncSessionLocal() as session:
        project = Project(name=f"rank-model-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        alice = Subject(name="Alice2", type=SubjectType.USER)
        session.add(alice)
        await session.flush()
        await session.commit()
        project_id = project.id
        alice_id = alice.id

    await _seed_request_fact(project_id=project_id, subject_id=alice_id, model_alias="model-a", total_tokens=400)
    await _seed_request_fact(project_id=project_id, subject_id=alice_id, model_alias="model-b", total_tokens=600)

    now = utcnow()
    start = now - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        ranking = await _usage_ranking_from_postgres(
            session, start=start, end=now + timedelta(hours=1), project_id=project_id, model="model-a", limit=20
        )

    assert len(ranking) == 1
    assert ranking[0]["total_tokens"] == 400
    assert ranking[0]["subject_name"] == "Alice2"


async def test_usage_ranking_empty_project_returns_empty_list():
    from uuid import uuid4

    from llm_gateway.db.models import utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.api.auth import _usage_ranking_from_postgres

    now = utcnow()
    start = now - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        ranking = await _usage_ranking_from_postgres(
            session, start=start, end=now + timedelta(hours=1), project_id=uuid4(), limit=20
        )

    assert ranking == []
