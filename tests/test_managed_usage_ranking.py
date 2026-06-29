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


async def _admin_headers(client):
    from llm_gateway.core.config import get_settings

    login = await client.post(
        "/auth/login",
        json={
            "username": get_settings().bootstrap_admin_username,
            "password": get_settings().bootstrap_admin_password,
        },
    )
    assert login.status_code == 200, login.text
    return {"x-session-token": login.json()["session_token"]}


async def _login_plain_user(client):
    from tests.test_backend_integration import _employee_username

    username = _employee_username()
    await client.post(
        "/auth/register",
        json={"username": username, "full_name": "普通用户", "password": "correct-horse-battery"},
    )
    login = await client.post(
        "/auth/login", json={"username": username, "password": "correct-horse-battery"}
    )
    assert login.status_code == 200, login.text
    return {"x-session-token": login.json()["session_token"]}, username


async def _make_project_manager(client, project_name):
    """Create a self-service user, then add them as manager of a fresh project.

    project_memberships.role is a plain str ("manager"); _managed_projects_payload
    filters role == "manager" AND project.state == ACTIVE. Returns
    (manager_headers, project_id, username).
    """
    from uuid import uuid4

    from sqlalchemy import select
    from sqlmodel import col

    from tests.test_backend_integration import _employee_username
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Project, ProjectMembership, Subject

    username = _employee_username()
    await client.post(
        "/auth/register",
        json={"username": username, "full_name": project_name, "password": "correct-horse-battery"},
    )
    login = await client.post(
        "/auth/login", json={"username": username, "password": "correct-horse-battery"}
    )
    assert login.status_code == 200, login.text
    manager_headers = {"x-session-token": login.json()["session_token"]}

    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        subject = (
            await session.execute(
                select(Subject).where(col(Subject.login_username) == username)
            )
        ).scalar_one()
        project = Project(name=f"mgr-project-{suffix}", owner_subject_id=subject.id)
        session.add(project)
        await session.flush()
        membership = ProjectMembership(
            project_id=project.id,
            subject_id=subject.id,
            role="manager",
        )
        session.add(membership)
        await session.commit()
        project_id = project.id

    return manager_headers, project_id, username


async def test_manager_can_query_ranking_for_managed_project(client):
    from sqlalchemy import select
    from sqlmodel import col

    from llm_gateway.db.models import Subject
    from llm_gateway.db.session import AsyncSessionLocal

    manager_headers, project_id, manager_username = await _make_project_manager(
        client, "Manager1"
    )

    async with AsyncSessionLocal() as session:
        manager = (
            await session.execute(
                select(Subject).where(col(Subject.login_username) == manager_username)
            )
        ).scalar_one()
        manager_subject_id = manager.id

    await _seed_request_fact(project_id=project_id, subject_id=manager_subject_id, total_tokens=200)

    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"project_id": str(project_id)},
        headers=manager_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(project_id)
    assert len(body["ranking"]) >= 1
    row = body["ranking"][0]
    assert row["total_tokens"] == 200
    assert set(row.keys()) == {
        "subject_id", "subject_name", "login_username",
        "request_count", "prompt_tokens", "completion_tokens",
        "total_tokens", "success_count", "failure_count",
    }


async def test_non_manager_cannot_query_project_ranking(client):
    _, project_id, _ = await _make_project_manager(client, "Manager2")
    other_headers, _ = await _login_plain_user(client)

    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"project_id": str(project_id)},
        headers=other_headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "not_project_manager"


async def test_ranking_rejects_time_window_over_90_days(client):
    from llm_gateway.db.models import utcnow

    manager_headers, project_id, _ = await _make_project_manager(client, "Manager3")

    start = (utcnow() - timedelta(days=100)).isoformat()
    end = utcnow().isoformat()
    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"project_id": str(project_id), "start": start, "end": end},
        headers=manager_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "time_window_exceeds_90_days"


async def test_ranking_without_session_returns_401(client):
    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"project_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 401


async def test_ranking_missing_project_id_returns_422(client):
    headers, _ = await _login_plain_user(client)
    response = await client.get("/auth/managed/usage/ranking", headers=headers)
    assert response.status_code == 422
