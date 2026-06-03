from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlmodel import col

from llm_gateway.db.models import (
    EndpointFamily,
    RequestFact,
    RequestOutcome,
    UsageSource,
    utcnow,
)
from llm_gateway.services.litellm_client import LiteLLMCallResult

from conftest import fetch_request_fact


pytestmark = pytest.mark.asyncio(loop_scope="session")


def _auth_headers(raw_key: str, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {raw_key}"}
    if request_id:
        headers["x-request-id"] = request_id
    return headers


def _employee_username() -> str:
    return f"l{uuid4().int % 100_000_000:08d}"


async def test_list_models_returns_entitled_aliases(client, gateway_fixture):
    response = await client.get(
        "/v1/models",
        headers=_auth_headers(gateway_fixture.raw_key),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    ids = [m["id"] for m in payload["data"]]
    assert gateway_fixture.model_alias in ids
    for m in payload["data"]:
        assert m["object"] == "model"
        assert "created" in m
        assert m["owned_by"] == "gateway"


async def test_list_models_rejects_invalid_key(client):
    response = await client.get(
        "/v1/models",
        headers={"Authorization": "Bearer gw-invalid"},
    )
    assert response.status_code == 401


async def test_health_and_admin_diagnostics(client, monkeypatch):
    from llm_gateway.core.config import get_settings

    async def no_metric_targets():
        return []

    monkeypatch.setattr(
        "llm_gateway.api.realtime._load_vllm_metric_targets", no_metric_targets
    )

    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["ok"] is True

    diagnostics = await client.get(
        "/admin/diagnostics", headers={"x-admin-token": get_settings().admin_token}
    )
    assert diagnostics.status_code == 200
    assert diagnostics.json()["litellm_version"] != "unknown"

    realtime = await client.get(
        "/admin/realtime/snapshot",
        headers={"x-admin-token": get_settings().admin_token},
    )
    assert realtime.status_code == 200
    assert "total_tokens_per_second" in realtime.json()

    unauthorized = await client.get("/admin/realtime/snapshot")
    assert unauthorized.status_code == 401


async def test_self_service_register_login_and_guest_team_model_access(client):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.models import Team
    from llm_gateway.db.session import AsyncSessionLocal
    from sqlalchemy import select

    headers = {"x-admin-token": get_settings().admin_token}
    suffix = uuid4().hex
    bootstrap = await client.post(
        "/auth/login",
        json={
            "username": get_settings().bootstrap_admin_username,
            "password": get_settings().bootstrap_admin_password,
        },
    )
    assert bootstrap.status_code == 200, bootstrap.text

    model = await client.post(
        "/admin/model-aliases",
        headers=headers,
        json={
            "alias": f"guest-model-{suffix}",
            "upstream_model_name": "guest-upstream",
            "litellm_model": "openai/guest-upstream",
        },
    )
    assert model.status_code == 200, model.text
    model_id = model.json()["id"]

    async with AsyncSessionLocal() as session:
        guest_team = (
            await session.execute(select(Team).where(col(Team.name) == "guest"))
        ).scalar_one()

    grant = await client.post(
        "/admin/model-team-grants",
        headers=headers,
        json={"model_alias_id": model_id, "team_id": str(guest_team.id)},
    )
    assert grant.status_code == 200, grant.text

    username = _employee_username()
    registered = await client.post(
        "/auth/register",
        json={
            "username": username,
            "full_name": "测试用户",
            "password": "correct-horse-battery",
        },
    )
    assert registered.status_code == 200, registered.text
    payload = registered.json()
    raw_key = payload["gateway_key"]["plaintext_key"]
    assert payload["profile"]["subject"]["login_username"] == username
    assert "guest" in payload["profile"]["teams"]
    assert f"guest-model-{suffix}" in payload["profile"]["models"]

    models = await client.get("/v1/models", headers=_auth_headers(raw_key))
    assert models.status_code == 200, models.text
    ids = [item["id"] for item in models.json()["data"]]
    assert f"guest-model-{suffix}" in ids

    logged_in = await client.post(
        "/auth/login", json={"username": username, "password": "correct-horse-battery"}
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["session_token"].startswith("sess-")


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
        await client.post(
            "/admin/teams", headers=session_headers, json={"name": f"team1-{suffix}"}
        )
    ).json()
    team3 = (
        await client.post(
            "/admin/teams", headers=session_headers, json={"name": f"team3-{suffix}"}
        )
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
                "litellm_model": f"openai/upstream-{label}",
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


async def test_legacy_user_must_complete_real_name_after_login(client):
    from llm_gateway.db.models import Subject, SubjectType
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import hash_password

    username = _employee_username()
    async with AsyncSessionLocal() as session:
        subject = Subject(
            name=username,
            type=SubjectType.USER,
            login_username=username,
            password_hash=hash_password("correct-horse-battery"),
        )
        session.add(subject)
        await session.commit()

    logged_in = await client.post(
        "/auth/login", json={"username": username, "password": "correct-horse-battery"}
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["profile"]["subject"]["requires_real_name"] is True

    updated = await client.patch(
        "/auth/profile",
        headers={"x-session-token": logged_in.json()["session_token"]},
        json={"full_name": "遗留用户"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["subject"]["name"] == "遗留用户"
    assert updated.json()["subject"]["requires_real_name"] is False


async def test_registration_rejects_non_employee_username(client):
    response = await client.post(
        "/auth/register",
        json={
            "username": "alice",
            "full_name": "Alice",
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 422


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


async def test_model_alias_delete_requires_cascade_for_upstreams(client):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}
    suffix = uuid4().hex
    model = await client.post(
        "/admin/model-aliases",
        headers=headers,
        json={
            "alias": f"delete-model-{suffix}",
            "upstream_model_name": "delete-upstream-model",
            "litellm_model": "openai/delete-upstream-model",
        },
    )
    assert model.status_code == 200, model.text
    model_id = model.json()["id"]

    upstream = await client.post(
        "/admin/upstreams",
        headers=headers,
        json={
            "model_alias_id": model_id,
            "name": f"delete-upstream-{suffix}",
            "base_url": "http://127.0.0.1:65530/v1",
        },
    )
    assert upstream.status_code == 200, upstream.text

    blocked = await client.delete(f"/admin/model-aliases/{model_id}", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "model_alias_has_upstreams"

    deleted = await client.delete(
        f"/admin/model-aliases/{model_id}",
        headers=headers,
        params={"cascade_upstreams": True},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_upstreams"] == 1


async def test_usage_ranking_falls_back_to_prompt_plus_completion_tokens_and_bounds_limit(
    client, gateway_fixture
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.models import RequestFact, utcnow
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

    invalid_limit = await client.get(
        "/admin/usage/ranking", headers=headers, params={"limit": 0}
    )
    assert invalid_limit.status_code == 422


async def test_self_service_usage_summary_is_scoped_to_current_user(client):
    from datetime import timedelta

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
        session.add(
            ProjectMembership(
                project_id=project.id, subject_id=manager.id, role="manager"
            )
        )
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
        _, outsider_token = await create_user_session(
            session, subject_id=outsider.id, ttl_hours=24
        )
        await session.commit()

    headers = {"x-session-token": manager_token}
    profile = await client.get("/auth/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["managed"]["projects"][0]["project"]["id"] == str(project.id)

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
    removed = await client.delete(
        f"/auth/managed/project-memberships/{created.json()['id']}",
        headers=headers,
    )
    assert removed.status_code == 200

    denied = await client.get(
        "/auth/managed/projects", headers={"x-session-token": outsider_token}
    )
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
        manager_membership = TeamMembership(
            team_id=team.id, subject_id=manager.id, role="manager"
        )
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
        _, manager_token = await create_user_session(
            session, subject_id=manager.id, ttl_hours=24
        )
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
        f"/auth/managed/team-memberships/{created.json()['id']}",
        headers=headers,
        json={"state": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["state"] == "disabled"


async def test_openai_chat_completion_uses_real_upstream_and_records_usage(
    client, gateway_fixture
):
    request_id = f"pytest-openai-{uuid4()}"
    response = await client.post(
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [
                {"role": "user", "content": "Reply with exactly one short sentence."}
            ],
            "max_tokens": 32,
            "temperature": 0,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["choices"]
    assert payload.get("usage", {}).get("total_tokens", 0) > 0

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.OPENAI_CHAT
    assert fact.outcome == RequestOutcome.SUCCESS
    assert fact.usage_source == UsageSource.LITELLM
    assert fact.total_tokens and fact.total_tokens > 0


async def test_invalid_gateway_key_records_auth_failure(client, gateway_fixture):
    request_id = f"pytest-auth-failure-{uuid4()}"
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer gw-invalid", "x-request-id": request_id},
        json={
            "model": gateway_fixture.model_alias,
            "messages": [
                {"role": "user", "content": "This should not reach upstream."}
            ],
            "max_tokens": 16,
        },
    )

    assert response.status_code == 401

    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.AUTH_FAILURE
    assert fact.subject_id is None
    assert fact.upstream_target_id is None


async def test_openai_stream_completion_records_success(client, gateway_fixture):
    request_id = f"pytest-openai-stream-{uuid4()}"
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [
                {"role": "user", "content": "Say stream-ok in a short sentence."}
            ],
            "max_tokens": 32,
            "temperature": 0,
            "stream": True,
        },
    ) as response:
        body = await response.aread()

    assert response.status_code == 200
    assert b"data:" in body
    assert b"[DONE]" in body

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.OPENAI_CHAT
    assert fact.outcome == RequestOutcome.SUCCESS
    assert fact.streaming is True


async def test_anthropic_messages_conversion_uses_litellm_and_records_usage(
    client, gateway_fixture
):
    request_id = f"pytest-anthropic-{uuid4()}"
    response = await client.post(
        "/v1/messages",
        headers={"x-api-key": gateway_fixture.raw_key, "x-request-id": request_id},
        json={
            "model": gateway_fixture.model_alias,
            "max_tokens": 32,
            "messages": [
                {"role": "user", "content": "Reply with exactly one short sentence."}
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("content")
    assert payload.get("usage", {}).get("input_tokens", 0) > 0

    fact = await fetch_request_fact(request_id)
    assert fact.endpoint_family == EndpointFamily.ANTHROPIC_MESSAGES
    assert fact.outcome == RequestOutcome.SUCCESS
    assert fact.usage_source == UsageSource.LITELLM
    assert fact.prompt_tokens and fact.prompt_tokens > 0


async def test_model_ip_allowlist_denies_disallowed_client(
    external_ip_client, gateway_fixture
):
    from llm_gateway.db.models import IPPolicyMode, ModelAlias
    from llm_gateway.db.session import AsyncSessionLocal

    request_id = f"pytest-ip-deny-{uuid4()}"
    async with AsyncSessionLocal() as session:
        model_alias = await session.get(ModelAlias, gateway_fixture.model_alias_id)
        assert model_alias is not None
        model_alias.ip_policy_mode = IPPolicyMode.ALLOWLIST
        model_alias.ip_allowlist_cidrs = ["203.0.113.1/32"]
        await session.commit()

    response = await external_ip_client.post(
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [
                {"role": "user", "content": "This should be denied before upstream."}
            ],
            "max_tokens": 16,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "model_ip_denied"

    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.POLICY_DENIAL
    assert fact.upstream_target_id is None


async def test_model_ip_allowlist_accepts_forwarded_client_from_trusted_vite_proxy(
    client, gateway_fixture, monkeypatch
):
    from llm_gateway.api.deps import settings_dep
    from llm_gateway.core.config import Settings
    from llm_gateway.db.models import IPPolicyMode, ModelAlias
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.main import app

    async def fake_completion_once(*, model_alias, upstream, body):
        return LiteLLMCallResult(
            response={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}}
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
            usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        )

    monkeypatch.setattr("llm_gateway.api.proxy.completion_once", fake_completion_once)

    def trusted_proxy_settings() -> Settings:
        settings = Settings()
        settings.trusted_proxy_headers = True
        settings.trusted_proxy_cidrs = "127.0.0.0/8,::1/128"
        return settings

    app.dependency_overrides[settings_dep] = trusted_proxy_settings

    request_id = f"pytest-ip-forwarded-allow-{uuid4()}"
    async with AsyncSessionLocal() as session:
        model_alias = await session.get(ModelAlias, gateway_fixture.model_alias_id)
        assert model_alias is not None
        model_alias.ip_policy_mode = IPPolicyMode.ALLOWLIST
        model_alias.ip_allowlist_cidrs = ["10.21.48.65/32"]
        await session.commit()

    try:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                **_auth_headers(gateway_fixture.raw_key, request_id),
                "x-forwarded-for": "10.21.48.65, 127.0.0.1",
            },
            json={
                "model": gateway_fixture.model_alias,
                "messages": [{"role": "user", "content": "This should pass."}],
                "max_tokens": 16,
            },
        )
    finally:
        app.dependency_overrides.pop(settings_dep, None)

    assert response.status_code == 200, response.text
    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.SUCCESS


async def test_openai_chat_completion_records_realtime_runtime_metrics(
    client, gateway_fixture, monkeypatch
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.services.rate_limit import redis_client
    from llm_gateway.services.runtime_metrics import ACTIVE_KEY, runtime_snapshot

    async def no_metric_targets():
        return []

    monkeypatch.setattr(
        "llm_gateway.api.realtime._load_vllm_metric_targets", no_metric_targets
    )

    observed_during_call: dict[str, object] = {}

    async def fake_completion_once(*, model_alias, upstream, body):
        observed_during_call.update(await runtime_snapshot(redis_client))
        return LiteLLMCallResult(
            response={
                "id": "chatcmpl-runtime-metrics",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}}
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
            usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        )

    await redis_client.delete(ACTIVE_KEY)
    monkeypatch.setattr("llm_gateway.api.proxy.completion_once", fake_completion_once)

    request_id = f"pytest-runtime-metrics-{uuid4()}"
    response = await client.post(
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [{"role": "user", "content": "metrics"}],
            "max_tokens": 16,
        },
    )
    assert response.status_code == 200, response.text
    assert observed_during_call["active_connections"] == 1
    active_rows = cast(list[dict[str, Any]], observed_during_call["upstreams"])
    assert any(
        item["upstream_id"] == str(gateway_fixture.upstream_id) for item in active_rows
    )

    snapshot = await client.get(
        "/admin/realtime/snapshot",
        headers={"x-admin-token": get_settings().admin_token},
        params={"window_seconds": 60},
    )
    assert snapshot.status_code == 200, snapshot.text
    payload = snapshot.json()
    assert payload["total_recent_tokens"] is None
    assert payload["total_tokens_per_second"] is None
    assert payload["active_connections"] == 0
    assert all(
        item["upstream_id"] != str(gateway_fixture.upstream_id)
        for item in payload["upstreams"]
    )


async def test_realtime_snapshot_includes_cached_vllm_metrics(
    client, gateway_fixture, monkeypatch
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.services.rate_limit import redis_client
    from llm_gateway.services.runtime_metrics import (
        VLLM_METRICS_CACHE_PREFIX,
        VLLM_METRICS_COUNTER_PREFIX,
        VLLM_METRICS_LOCK_PREFIX,
        VLLMMetricsTarget,
    )

    async def metric_targets():
        return [
            VLLMMetricsTarget(
                upstream_id=str(gateway_fixture.upstream_id),
                upstream_name="pytest-vllm",
                model_alias=gateway_fixture.model_alias,
                base_url="http://pytest-vllm:8000/v1",
                extra_headers={},
            )
        ]

    async def fake_metrics_text(target):
        assert target.upstream_id == str(gateway_fixture.upstream_id)
        return """
vllm:num_requests_running 2
vllm:num_requests_waiting 1
vllm:kv_cache_usage_perc 0.8
vllm:prefix_cache_queries 20
vllm:prefix_cache_hits 15
vllm:prompt_tokens_total 200
vllm:generation_tokens_total 100
"""

    await redis_client.delete(
        f"{VLLM_METRICS_CACHE_PREFIX}:{gateway_fixture.upstream_id}",
        f"{VLLM_METRICS_LOCK_PREFIX}:{gateway_fixture.upstream_id}",
        f"{VLLM_METRICS_COUNTER_PREFIX}:{gateway_fixture.upstream_id}",
    )
    monkeypatch.setattr(
        "llm_gateway.api.realtime._load_vllm_metric_targets", metric_targets
    )
    monkeypatch.setattr(
        "llm_gateway.services.runtime_metrics._fetch_vllm_metrics_text",
        fake_metrics_text,
    )

    response = await client.get(
        "/admin/realtime/snapshot",
        headers={"x-admin-token": get_settings().admin_token},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["vllm"]["observed_upstreams"] == 1
    assert payload["vllm"]["ok_upstreams"] == 1
    assert payload["vllm"]["running"] == 2
    assert payload["vllm"]["waiting"] == 1
    assert payload["vllm"]["max_kv_cache_usage"] == 0.8
    assert payload["upstreams"][0]["vllm"]["kind"] == "vllm"
    assert payload["upstreams"][0]["vllm"]["prefix_cache_hit_ratio"] == 0.75


async def test_key_scoped_rate_policy_blocks_before_upstream(client, gateway_fixture):
    from llm_gateway.db.models import RatePolicy
    from llm_gateway.db.session import AsyncSessionLocal

    request_id = f"pytest-rate-deny-{uuid4()}"
    async with AsyncSessionLocal() as session:
        session.add(
            RatePolicy(
                scope="key",
                scope_id=gateway_fixture.key_id,
                requests_per_minute=0,
                concurrency_limit=1,
            )
        )
        await session.commit()

    response = await client.post(
        "/v1/chat/completions",
        headers=_auth_headers(gateway_fixture.raw_key, request_id),
        json={
            "model": gateway_fixture.model_alias,
            "messages": [
                {
                    "role": "user",
                    "content": "This should be rate limited before upstream.",
                }
            ],
            "max_tokens": 16,
        },
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "request_rate_exceeded"

    fact = await fetch_request_fact(request_id)
    assert fact.outcome == RequestOutcome.RATE_LIMITED


async def test_admin_updates_router_command_rate_policy_and_upstream_health(
    client, gateway_fixture
):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}

    health = await client.get(
        f"/admin/upstreams/{gateway_fixture.upstream_id}/health", headers=headers
    )
    assert health.status_code == 200, health.text
    health_payload = health.json()
    assert health_payload["upstream"]["api_key_value"] is None
    assert health_payload["upstream"]["has_api_key"] is True
    assert health_payload["health"]["status_code"] < 500

    alias_patch = await client.patch(
        f"/admin/model-aliases/{gateway_fixture.model_alias_id}",
        headers=headers,
        json={"notes": "updated by integration test"},
    )
    assert alias_patch.status_code == 200, alias_patch.text
    assert alias_patch.json()["notes"] == "updated by integration test"

    router_config = await client.post(
        "/admin/router-command-configs",
        headers=headers,
        json={
            "model_alias_id": str(gateway_fixture.model_alias_id),
            "name": "pytest-router",
            "worker_urls": ["http://127.0.0.1:9001", "http://127.0.0.1:9002"],
            "policy": "consistent_hash",
            "port": 19001,
            "extra_args": {"request_timeout": 30},
        },
    )
    assert router_config.status_code == 200, router_config.text
    command = router_config.json()["command"]
    assert "vllm-router" in command
    assert "--worker-urls" in command
    assert "http://127.0.0.1:9001" in command

    rate_policy = await client.post(
        "/admin/rate-policies",
        headers=headers,
        json={
            "scope": "project",
            "scope_id": str(gateway_fixture.project_id),
            "requests_per_minute": 99,
            "concurrency_limit": 7,
        },
    )
    assert rate_policy.status_code == 200, rate_policy.text
    policy_id = rate_policy.json()["id"]

    patched_policy = await client.patch(
        f"/admin/rate-policies/{policy_id}",
        headers=headers,
        json={"requests_per_minute": 55},
    )
    assert patched_policy.status_code == 200, patched_policy.text
    assert patched_policy.json()["requests_per_minute"] == 55


async def test_admin_can_edit_upstream_endpoint_after_launch(client, gateway_fixture):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}
    new_base_url = "https://example.internal/v1"
    metrics_url = "https://example.internal:29000/metrics"
    patched = await client.patch(
        f"/admin/upstreams/{gateway_fixture.upstream_id}",
        headers=headers,
        json={
            "name": "patched-upstream",
            "base_url": new_base_url,
            "metrics_url": metrics_url,
            "health_path": "/healthz",
            "api_key_ref": "patched-key-ref",
            "extra_headers": {"x-test": "patched"},
        },
    )

    assert patched.status_code == 200, patched.text
    payload = patched.json()
    assert payload["name"] == "patched-upstream"
    assert payload["base_url"] == new_base_url
    assert payload["metrics_url"] == metrics_url
    assert payload["health_path"] == "/healthz"
    assert payload["api_key_ref"] == "patched-key-ref"
    assert payload["extra_headers"] == {"x-test": "patched"}
    assert payload["api_key_value"] is None


async def test_admin_can_delete_used_upstream_without_deleting_request_facts(
    client,
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.models import (
        ModelAlias,
        Project,
        Subject,
        SubjectType,
        UpstreamTarget,
    )
    from llm_gateway.db.session import AsyncSessionLocal

    suffix = uuid4().hex
    request_id = f"pytest-used-upstream-delete-{uuid4()}"
    async with AsyncSessionLocal() as session:
        subject = Subject(
            name=f"delete-upstream-subject-{suffix}", type=SubjectType.USER
        )
        session.add(subject)
        await session.flush()
        project = Project(
            name=f"delete-upstream-project-{suffix}", owner_subject_id=subject.id
        )
        session.add(project)
        await session.flush()
        model = ModelAlias(
            alias=f"delete-upstream-model-{suffix}",
            upstream_model_name=f"delete-upstream-model-{suffix}",
            litellm_model=f"openai/delete-upstream-model-{suffix}",
        )
        session.add(model)
        await session.flush()
        upstream = UpstreamTarget(
            model_alias_id=model.id,
            name=f"delete-upstream-{suffix}",
            base_url="https://example.internal/v1",
            api_key_value="test-key",
        )
        session.add(upstream)
        await session.flush()
        fact = RequestFact(
            request_id=request_id,
            started_at=fetch_now(),
            ended_at=fetch_now(),
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            subject_id=subject.id,
            project_id=project.id,
            model_alias=model.alias,
            upstream_target_id=upstream.id,
            streaming=False,
            outcome=RequestOutcome.SUCCESS,
            prompt_tokens=3,
            completion_tokens=4,
            total_tokens=7,
        )
        session.add(fact)
        await session.commit()
        upstream_id = upstream.id
        model_alias = model.alias

    headers = {"x-admin-token": get_settings().admin_token}
    deleted = await client.delete(f"/admin/upstreams/{upstream_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["detached_usage_facts"] >= 1

    fact = await fetch_request_fact(request_id)
    assert fact.model_alias == model_alias
    assert fact.upstream_target_id is None


async def test_admin_can_cascade_delete_used_model_alias_preserving_usage(
    client,
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.models import (
        ModelAlias,
        Project,
        Subject,
        SubjectType,
        UpstreamTarget,
    )
    from llm_gateway.db.session import AsyncSessionLocal

    suffix = uuid4().hex
    request_id = f"pytest-used-alias-delete-{uuid4()}"
    async with AsyncSessionLocal() as session:
        subject = Subject(name=f"delete-alias-subject-{suffix}", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        project = Project(
            name=f"delete-alias-project-{suffix}", owner_subject_id=subject.id
        )
        session.add(project)
        await session.flush()
        model = ModelAlias(
            alias=f"delete-alias-model-{suffix}",
            upstream_model_name=f"delete-alias-model-{suffix}",
            litellm_model=f"openai/delete-alias-model-{suffix}",
        )
        session.add(model)
        await session.flush()
        upstream = UpstreamTarget(
            model_alias_id=model.id,
            name=f"delete-alias-upstream-{suffix}",
            base_url="https://example.internal/v1",
            api_key_value="test-key",
        )
        session.add(upstream)
        await session.flush()
        session.add(
            RequestFact(
                request_id=request_id,
                started_at=fetch_now(),
                ended_at=fetch_now(),
                endpoint_family=EndpointFamily.OPENAI_RESPONSES,
                subject_id=subject.id,
                project_id=project.id,
                model_alias=model.alias,
                upstream_target_id=upstream.id,
                streaming=False,
                outcome=RequestOutcome.SUCCESS,
                prompt_tokens=5,
                completion_tokens=6,
                total_tokens=11,
            )
        )
        await session.commit()
        model_alias_id = model.id
        model_alias = model.alias

    headers = {"x-admin-token": get_settings().admin_token}
    blocked = await client.delete(
        f"/admin/model-aliases/{model_alias_id}", headers=headers
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "model_alias_has_upstreams"

    deleted = await client.delete(
        f"/admin/model-aliases/{model_alias_id}",
        headers=headers,
        params={"cascade_upstreams": True},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_upstreams"] >= 1
    assert deleted.json()["detached_usage_facts"] >= 1

    fact = await fetch_request_fact(request_id)
    assert fact.model_alias == model_alias
    assert fact.upstream_target_id is None


def fetch_now():
    from llm_gateway.db.models import utcnow

    return utcnow()
