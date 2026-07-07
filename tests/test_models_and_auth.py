from __future__ import annotations

from uuid import uuid4

import pytest
from sqlmodel import col

from tests.helpers import _auth_headers, _employee_username

pytestmark = pytest.mark.asyncio(loop_scope="session")


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

    async def no_metric_targets(redis=None):
        return []

    monkeypatch.setattr("llm_gateway.api.realtime._load_vllm_metric_targets", no_metric_targets)

    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["ok"] is True

    diagnostics = await client.get(
        "/admin/diagnostics", headers={"x-admin-token": get_settings().admin_token}
    )
    assert diagnostics.status_code == 200
    assert "app_name" in diagnostics.json()

    realtime = await client.get(
        "/admin/realtime/snapshot",
        headers={"x-admin-token": get_settings().admin_token},
    )
    assert realtime.status_code == 200
    assert "total_tokens_per_second" in realtime.json()

    unauthorized = await client.get("/admin/realtime/snapshot")
    assert unauthorized.status_code == 401


async def test_self_service_register_login_and_guest_team_model_access(client):
    from sqlalchemy import select

    from llm_gateway.core.config import get_settings
    from llm_gateway.db.models import Team
    from llm_gateway.db.session import AsyncSessionLocal

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
            "litellm_model": "guest-upstream",
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
    team_memberships = payload["profile"]["team_memberships"]
    assert isinstance(team_memberships, list)
    assert any(m["name"] == "guest" and m["id"] for m in team_memberships)
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
