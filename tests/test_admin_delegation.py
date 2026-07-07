from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from llm_gateway.db.models import (
    EndpointFamily,
    RequestFact,
    RequestOutcome,
    UsageSource,
    utcnow,
)
from tests.helpers import _auth_headers, _employee_username

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_admin_session_can_manage_team_union_permissions(client):
    from llm_gateway.core.config import get_settings

    suffix = uuid4().hex
    login = await client.post(
        "/auth/login",
        json={
            "username": get_settings().bootstrap_admin_username,
            "password": get_settings().bootstrap_admin_password,
        },
    )
    assert login.status_code == 200, login.text
    session_headers = {"x-session-token": login.json()["session_token"]}

    registered = await client.post(
        "/auth/register",
        json={
            "username": _employee_username(),
            "full_name": "权限用户",
            "password": "correct-horse-battery",
        },
    )
    assert registered.status_code == 200, registered.text
    subject_id = registered.json()["profile"]["subject"]["id"]
    raw_key = registered.json()["gateway_key"]["plaintext_key"]

    team1 = (
        await client.post("/admin/teams", headers=session_headers, json={"name": f"team1-{suffix}"})
    ).json()
    team3 = (
        await client.post("/admin/teams", headers=session_headers, json={"name": f"team3-{suffix}"})
    ).json()

    for team in [team1, team3]:
        response = await client.post(
            "/admin/team-memberships",
            headers=session_headers,
            json={"team_id": team["id"], "subject_id": subject_id},
        )
        assert response.status_code == 200, response.text

    granted_aliases = []
    for team, label in [(team1, "a"), (team1, "b"), (team3, "e")]:
        model = await client.post(
            "/admin/model-aliases",
            headers=session_headers,
            json={
                "alias": f"model-{label}-{suffix}",
                "upstream_model_name": f"upstream-{label}",
                "litellm_model": f"upstream-{label}",
            },
        )
        assert model.status_code == 200, model.text
        granted_aliases.append(model.json()["alias"])
        grant = await client.post(
            "/admin/model-team-grants",
            headers=session_headers,
            json={"model_alias_id": model.json()["id"], "team_id": team["id"]},
        )
        assert grant.status_code == 200, grant.text

    models = await client.get("/v1/models", headers=_auth_headers(raw_key))
    assert models.status_code == 200, models.text
    ids = {item["id"] for item in models.json()["data"]}
    assert set(granted_aliases).issubset(ids)


async def test_admin_can_reset_password_and_delete_unused_subject(client):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}
    username = _employee_username()
    created = await client.post(
        "/admin/subjects",
        headers=headers,
        json={
            "name": "待删用户",
            "login_username": username,
            "password": "old-correct-horse",
            "type": "user",
        },
    )
    assert created.status_code == 200, created.text
    subject_id = created.json()["id"]

    reset = await client.patch(
        f"/admin/subjects/{subject_id}/password",
        headers=headers,
        json={"new_password": "new-correct-horse"},
    )
    assert reset.status_code == 200, reset.text

    login = await client.post(
        "/auth/login", json={"username": username, "password": "new-correct-horse"}
    )
    assert login.status_code == 200, login.text

    deleted = await client.delete(f"/admin/subjects/{subject_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text


async def test_delegated_project_manager_can_manage_members_and_usage(client):
    from llm_gateway.db.models import Project, ProjectMembership, Subject, SubjectType
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import create_user_session

    now = utcnow()
    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        manager = Subject(
            name=f"Project Manager {suffix}",
            type=SubjectType.USER,
            login_username=f"l{uuid4().int % 100_000_000:08d}",
        )
        target = Subject(
            name=f"Managed Target {suffix}",
            type=SubjectType.USER,
            login_username=f"l{uuid4().int % 100_000_000:08d}",
        )
        outsider = Subject(
            name=f"Not Manager {suffix}",
            type=SubjectType.USER,
            login_username=f"l{uuid4().int % 100_000_000:08d}",
        )
        session.add_all([manager, target, outsider])
        await session.flush()
        project = Project(name=f"managed-project-{uuid4().hex}")
        session.add(project)
        await session.flush()
        session.add(ProjectMembership(project_id=project.id, subject_id=manager.id, role="manager"))
        session.add(
            RequestFact(
                request_id=f"managed-project-usage-{uuid4()}",
                started_at=now,
                ended_at=now,
                endpoint_family=EndpointFamily.OPENAI_CHAT,
                subject_id=target.id,
                subject_type=SubjectType.USER,
                project_id=project.id,
                model_alias="managed-model",
                streaming=False,
                outcome=RequestOutcome.SUCCESS,
                usage_source=UsageSource.LITELLM,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            )
        )
        manager_session, manager_token = await create_user_session(
            session, subject_id=manager.id, ttl_hours=24
        )
        _, outsider_token = await create_user_session(session, subject_id=outsider.id, ttl_hours=24)
        await session.commit()

    headers = {"x-session-token": manager_token}
    profile = await client.get("/auth/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["managed"]["projects"][0]["project"]["id"] == str(project.id)

    roles = await client.get("/auth/managed/roles", headers=headers)
    assert roles.status_code == 200
    assert roles.json() == [
        {"value": "member", "label": "member"},
        {"value": "manager", "label": "manager"},
    ]

    usage = await client.get(
        "/auth/managed/usage/summary",
        headers=headers,
        params={
            "scope": "project",
            "resource_id": str(project.id),
            "start": (now - timedelta(minutes=1)).isoformat(),
            "end": (now + timedelta(minutes=1)).isoformat(),
        },
    )
    assert usage.status_code == 200, usage.text
    assert usage.json()["total_tokens"] == 15

    candidates = await client.get(
        "/auth/managed/subjects",
        headers=headers,
        params={"q": f"Managed Target {suffix}"},
    )
    assert candidates.status_code == 200
    assert candidates.json()[0]["id"] == str(target.id)

    created = await client.post(
        "/auth/managed/project-memberships",
        headers=headers,
        json={
            "resource_id": str(project.id),
            "subject_id": str(target.id),
            "role": "member",
        },
    )
    assert created.status_code == 200, created.text
    created_payload = created.json()
    assert created_payload["subject_id"] == str(target.id)
    assert created_payload["subject_name"] == target.name
    assert created_payload["subject_login_username"] == target.login_username
    assert created_payload["role"] == "member"

    memberships = await client.get(
        "/auth/managed/project-memberships",
        headers=headers,
        params={"resource_id": str(project.id)},
    )
    assert memberships.status_code == 200
    assert any(
        item["subject_name"] == target.name
        and item["subject_login_username"] == target.login_username
        for item in memberships.json()
    )

    invalid_role = await client.post(
        "/auth/managed/project-memberships",
        headers=headers,
        json={
            "resource_id": str(project.id),
            "subject_id": str(target.id),
            "role": "owner",
        },
    )
    assert invalid_role.status_code == 422

    removed = await client.delete(
        f"/auth/managed/project-memberships/{created_payload['id']}",
        headers=headers,
    )
    assert removed.status_code == 200

    denied = await client.get("/auth/managed/projects", headers={"x-session-token": outsider_token})
    assert denied.status_code == 200
    forbidden = await client.post(
        "/auth/managed/project-memberships",
        headers={"x-session-token": outsider_token},
        json={
            "resource_id": str(project.id),
            "subject_id": str(target.id),
            "role": "member",
        },
    )
    assert forbidden.status_code == 403
    assert manager_session.id


async def test_delegated_team_manager_can_manage_members_and_usage(client):
    from llm_gateway.db.models import (
        Project,
        Subject,
        SubjectType,
        Team,
        TeamMembership,
    )
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import create_user_session

    now = utcnow()
    async with AsyncSessionLocal() as session:
        manager = Subject(
            name="Team Manager",
            type=SubjectType.USER,
            login_username=f"l{uuid4().int % 100_000_000:08d}",
        )
        target = Subject(
            name="Team Target",
            type=SubjectType.USER,
            login_username=f"l{uuid4().int % 100_000_000:08d}",
        )
        session.add_all([manager, target])
        await session.flush()
        project = Project(name=f"managed-team-project-{uuid4().hex}")
        team = Team(name=f"managed-team-{uuid4().hex}")
        session.add_all([project, team])
        await session.flush()
        manager_membership = TeamMembership(team_id=team.id, subject_id=manager.id, role="manager")
        session.add(manager_membership)
        session.add(
            RequestFact(
                request_id=f"managed-team-usage-{uuid4()}",
                started_at=now,
                ended_at=now,
                endpoint_family=EndpointFamily.OPENAI_RESPONSES,
                subject_id=target.id,
                subject_type=SubjectType.USER,
                project_id=project.id,
                model_alias="managed-model",
                streaming=False,
                outcome=RequestOutcome.SUCCESS,
                usage_source=UsageSource.LITELLM,
                prompt_tokens=7,
                completion_tokens=4,
                total_tokens=11,
            )
        )
        _, manager_token = await create_user_session(session, subject_id=manager.id, ttl_hours=24)
        await session.commit()

    headers = {"x-session-token": manager_token}
    teams = await client.get("/auth/managed/teams", headers=headers)
    assert teams.status_code == 200
    assert teams.json()[0]["team"]["id"] == str(team.id)

    created = await client.post(
        "/auth/managed/team-memberships",
        headers=headers,
        json={
            "resource_id": str(team.id),
            "subject_id": str(target.id),
            "role": "member",
        },
    )
    assert created.status_code == 200, created.text
    created_payload = created.json()
    assert created_payload["subject_id"] == str(target.id)
    assert created_payload["subject_name"] == target.name
    assert created_payload["subject_login_username"] == target.login_username
    assert created_payload["role"] == "member"

    memberships = await client.get(
        "/auth/managed/team-memberships",
        headers=headers,
        params={"resource_id": str(team.id)},
    )
    assert memberships.status_code == 200
    assert any(
        item["subject_name"] == target.name
        and item["subject_login_username"] == target.login_username
        for item in memberships.json()
    )

    invalid_role = await client.post(
        "/auth/managed/team-memberships",
        headers=headers,
        json={
            "resource_id": str(team.id),
            "subject_id": str(target.id),
            "role": "owner",
        },
    )
    assert invalid_role.status_code == 422

    usage = await client.get(
        "/auth/managed/usage/summary",
        headers=headers,
        params={
            "scope": "team",
            "resource_id": str(team.id),
            "start": (now - timedelta(minutes=1)).isoformat(),
            "end": (now + timedelta(minutes=1)).isoformat(),
        },
    )
    assert usage.status_code == 200, usage.text
    assert usage.json()["total_tokens"] == 11

    disabled = await client.patch(
        f"/auth/managed/team-memberships/{created_payload['id']}",
        headers=headers,
        json={"state": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["state"] == "disabled"
