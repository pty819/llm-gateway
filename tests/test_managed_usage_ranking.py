from __future__ import annotations

from datetime import timedelta

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_request_fact(
    *,
    project_id,
    subject_id,
    model_alias="test-model",
    total_tokens=100,
    outcome="success",
):
    """Insert a minimal RequestFact row for aggregation tests."""
    from llm_gateway.db.models import EndpointFamily, RequestOutcome, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
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
            outcome=RequestOutcome.SUCCESS
            if outcome == "success"
            else RequestOutcome.UPSTREAM_FAILURE,
            usage={
                "prompt_tokens": 10,
                "completion_tokens": total_tokens - 10,
                "total_tokens": total_tokens,
            },
        )
        await session.commit()


async def test_usage_ranking_groups_by_subject_and_sorts_by_total_tokens():
    """直接调用查询函数：两个 subject 在同一 project，按 total_tokens 降序。"""
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType, utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.usage_queries import (
        usage_ranking_from_postgres as _usage_ranking_from_postgres,
    )

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
            session,
            start=start,
            end=now + timedelta(hours=1),
            project_ids=[project_id],
            limit=20,
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
    from llm_gateway.services.usage_queries import (
        usage_ranking_from_postgres as _usage_ranking_from_postgres,
    )

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

    await _seed_request_fact(
        project_id=project_id,
        subject_id=alice_id,
        model_alias="model-a",
        total_tokens=400,
    )
    await _seed_request_fact(
        project_id=project_id,
        subject_id=alice_id,
        model_alias="model-b",
        total_tokens=600,
    )

    now = utcnow()
    start = now - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        ranking = await _usage_ranking_from_postgres(
            session,
            start=start,
            end=now + timedelta(hours=1),
            project_ids=[project_id],
            model="model-a",
            limit=20,
        )

    assert len(ranking) == 1
    assert ranking[0]["total_tokens"] == 400
    assert ranking[0]["subject_name"] == "Alice2"


async def test_usage_ranking_empty_project_returns_empty_list():
    from uuid import uuid4

    from llm_gateway.db.models import utcnow
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.usage_queries import (
        usage_ranking_from_postgres as _usage_ranking_from_postgres,
    )

    now = utcnow()
    start = now - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        ranking = await _usage_ranking_from_postgres(
            session,
            start=start,
            end=now + timedelta(hours=1),
            project_ids=[uuid4()],
            limit=20,
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


async def _create_user_with_session(full_name):
    """Create a user + session directly via DB (bypasses /auth/register rate limit).

    Mirrors the register endpoint minus the rate-limit check; used by helpers
    below so the full suite doesn't trip the per-IP register cap.
    """
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import (
        create_registered_user,
        create_user_session,
    )
    from tests.helpers import _employee_username

    username = _employee_username()
    async with AsyncSessionLocal() as session:
        subject, _project, _key, _raw = await create_registered_user(
            session,
            username=username,
            full_name=full_name,
            password="correct-horse-battery",
        )
        user_session, raw_session = await create_user_session(
            session,
            subject_id=subject.id,
            ttl_hours=get_settings().session_ttl_hours,
        )
        await session.commit()
        headers = {"x-session-token": raw_session}
    return headers, username, subject.id


async def _login_plain_user(client):
    headers, username, _ = await _create_user_with_session("普通用户")
    return headers, username


async def _make_project_manager(client, project_name):
    """Create a user, then add them as manager of a fresh project.

    project_memberships.role is a plain str ("manager"); _managed_projects_payload
    filters role == "manager" AND project.state == ACTIVE. Returns
    (manager_headers, project_id, username).
    """
    from uuid import uuid4

    from llm_gateway.db.models import Project, ProjectMembership
    from llm_gateway.db.session import AsyncSessionLocal

    manager_headers, username, subject_id = await _create_user_with_session(project_name)

    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        project = Project(name=f"mgr-project-{suffix}", owner_subject_id=subject_id)
        session.add(project)
        await session.flush()
        membership = ProjectMembership(
            project_id=project.id,
            subject_id=subject_id,
            role="manager",
        )
        session.add(membership)
        await session.commit()
        project_id = project.id

    return manager_headers, project_id, subject_id


async def test_manager_can_query_ranking_for_managed_project(client):
    manager_headers, project_id, manager_subject_id = await _make_project_manager(
        client, "Manager1"
    )

    await _seed_request_fact(project_id=project_id, subject_id=manager_subject_id, total_tokens=200)

    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"scope": "project", "resource_id": str(project_id)},
        headers=manager_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "project"
    assert body["resource_id"] == str(project_id)
    assert len(body["ranking"]) >= 1
    row = body["ranking"][0]
    assert row["total_tokens"] == 200
    assert set(row.keys()) == {
        "subject_id",
        "subject_name",
        "login_username",
        "request_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "success_count",
        "failure_count",
    }


async def test_non_manager_cannot_query_project_ranking(client):
    _, project_id, _ = await _make_project_manager(client, "Manager2")
    other_headers, _ = await _login_plain_user(client)

    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"scope": "project", "resource_id": str(project_id)},
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
        params={
            "scope": "project",
            "resource_id": str(project_id),
            "start": start,
            "end": end,
        },
        headers=manager_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "time_window_exceeds_90_days"


async def test_ranking_without_session_returns_401(client):
    response = await client.get(
        "/auth/managed/usage/ranking",
        params={
            "scope": "project",
            "resource_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 401


async def test_ranking_missing_resource_id_returns_422(client):
    headers, _ = await _login_plain_user(client)
    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"scope": "project"},
        headers=headers,
    )
    assert response.status_code == 422


async def _seed_team_fact(*, subject_id, model_alias="team-model", total_tokens=100):
    """Insert a RequestFact row attributed to a subject, with no project.

    Team ranking filters by subject_id (derived from team membership), so the
    project_id is irrelevant and left null to avoid needing a throwaway project.
    Mirrors the direct-insert style of test_usage_analytics.
    """
    from uuid import uuid4

    from llm_gateway.db.models import (
        EndpointFamily,
        RequestFact,
        RequestOutcome,
        UsageSource,
        utcnow,
    )
    from llm_gateway.db.session import AsyncSessionLocal

    now = utcnow()
    async with AsyncSessionLocal() as session:
        session.add(
            RequestFact(
                request_id=f"team-rank-{subject_id}-{uuid4()}",
                started_at=now,
                ended_at=now,
                endpoint_family=EndpointFamily.OPENAI_CHAT,
                subject_id=subject_id,
                subject_type="user",
                project_id=None,
                model_alias=model_alias,
                streaming=False,
                outcome=RequestOutcome.SUCCESS,
                usage_source=UsageSource.LITELLM,
                prompt_tokens=10,
                completion_tokens=total_tokens - 10,
                total_tokens=total_tokens,
            )
        )
        await session.commit()


async def _make_team_manager_with_members(client, manager_name="TeamMgr", member_count=2):
    """Create a user, make them manager of a fresh team, and add ``member_count``
    plain member subjects (ACTIVE, role=member). Returns
    (manager_headers, team_id, [member_subject_id, ...]).
    """
    from uuid import uuid4

    from llm_gateway.db.models import Subject, SubjectType, Team, TeamMembership
    from llm_gateway.db.session import AsyncSessionLocal

    manager_headers, _, manager_subject_id = await _create_user_with_session(manager_name)

    async with AsyncSessionLocal() as session:
        team = Team(name=f"mgr-team-{uuid4().hex}")
        session.add(team)
        await session.flush()
        session.add(TeamMembership(team_id=team.id, subject_id=manager_subject_id, role="manager"))
        member_ids: list = []
        for i in range(member_count):
            member = Subject(
                name=f"TeamMember{i}-{uuid4().hex[:6]}",
                type=SubjectType.USER,
                login_username=f"l{uuid4().int % 100_000_000:08d}",
            )
            session.add(member)
            await session.flush()
            session.add(TeamMembership(team_id=team.id, subject_id=member.id, role="member"))
            member_ids.append(member.id)
        await session.commit()
        team_id = team.id

    return manager_headers, team_id, member_ids


async def test_team_manager_can_query_ranking_for_managed_team(client):
    manager_headers, team_id, member_ids = await _make_team_manager_with_members(
        client, "TeamRankMgr"
    )
    member_a, member_b = member_ids[0], member_ids[1]

    await _seed_team_fact(subject_id=member_a, total_tokens=500)
    await _seed_team_fact(subject_id=member_b, total_tokens=200)

    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"scope": "team", "resource_id": str(team_id)},
        headers=manager_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "team"
    assert body["resource_id"] == str(team_id)
    ranking = body["ranking"]
    assert len(ranking) == 2
    # sorted by total_tokens desc
    assert ranking[0]["subject_id"] == str(member_a)
    assert ranking[0]["total_tokens"] == 500
    assert ranking[1]["subject_id"] == str(member_b)
    assert ranking[1]["total_tokens"] == 200
    assert set(ranking[0].keys()) == {
        "subject_id",
        "subject_name",
        "login_username",
        "request_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "success_count",
        "failure_count",
    }


async def test_non_manager_cannot_query_team_ranking(client):
    manager_headers, team_id, _ = await _make_team_manager_with_members(
        client, "TeamRankMgr2", member_count=1
    )
    other_headers, _ = await _login_plain_user(client)

    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"scope": "team", "resource_id": str(team_id)},
        headers=other_headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "not_team_manager"


async def test_team_ranking_excludes_disabled_members(client):
    """A disabled team membership should drop the member from the ranking, since
    _team_subject_ids filters TeamMembership.state == ACTIVE."""
    manager_headers, team_id, member_ids = await _make_team_manager_with_members(
        client, "TeamRankMgr3", member_count=1
    )
    member_a = member_ids[0]

    await _seed_team_fact(subject_id=member_a, total_tokens=300)

    # Disable the member via the self-service membership endpoint.
    memberships = await client.get(
        "/auth/managed/team-memberships",
        params={"resource_id": str(team_id)},
        headers=manager_headers,
    )
    assert memberships.status_code == 200
    member_membership = next(m for m in memberships.json() if m["subject_id"] == str(member_a))
    disabled = await client.patch(
        f"/auth/managed/team-memberships/{member_membership['id']}",
        headers=manager_headers,
        json={"state": "disabled"},
    )
    assert disabled.status_code == 200

    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"scope": "team", "resource_id": str(team_id)},
        headers=manager_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["ranking"] == []
