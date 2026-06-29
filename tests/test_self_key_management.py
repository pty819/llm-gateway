from __future__ import annotations

from uuid import uuid4

import pytest


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _login_self_service_user(client):
    """Register a fresh self-service user and return (session_headers, username)."""
    from tests.test_backend_integration import _employee_username

    username = _employee_username()
    register = await client.post(
        "/auth/register",
        json={"username": username, "full_name": "自助用户", "password": "correct-horse-battery"},
    )
    assert register.status_code == 200, register.text
    login = await client.post(
        "/auth/login",
        json={"username": username, "password": "correct-horse-battery"},
    )
    assert login.status_code == 200, login.text
    headers = {"x-session-token": login.json()["session_token"]}
    return headers, username


async def _issue_own_key(client, headers, name="测试密钥"):
    response = await client.post("/auth/keys", json={"name": name}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["key"]


async def test_user_can_disable_own_key(client):
    headers, _ = await _login_self_service_user(client)
    key = await _issue_own_key(client, headers)

    response = await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "disabled"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["key"]["state"] == "disabled"


async def test_user_can_re_enable_own_disabled_key(client):
    headers, _ = await _login_self_service_user(client)
    key = await _issue_own_key(client, headers)

    # 先禁用
    await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "disabled"},
        headers=headers,
    )
    # 再启用
    response = await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "active"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["key"]["state"] == "active"


async def test_disable_other_users_key_returns_404(client):
    victim_headers, _ = await _login_self_service_user(client)
    victim_key = await _issue_own_key(client, victim_headers)
    attacker_headers, _ = await _login_self_service_user(client)

    response = await client.patch(
        f"/auth/keys/{victim_key['id']}/state",
        json={"state": "disabled"},
        headers=attacker_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "key_not_found"


async def test_disable_nonexistent_key_returns_404(client):
    headers, _ = await _login_self_service_user(client)

    response = await client.patch(
        f"/auth/keys/{uuid4()}/state",
        json={"state": "disabled"},
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "key_not_found"


async def test_disable_key_without_session_returns_401(client):
    headers, _ = await _login_self_service_user(client)
    key = await _issue_own_key(client, headers)

    response = await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "disabled"},
    )

    assert response.status_code == 401


async def test_disable_key_writes_audit_event(client):
    from sqlalchemy import select

    from llm_gateway.db.models import AuditEvent
    from llm_gateway.db.session import AsyncSessionLocal

    headers, _ = await _login_self_service_user(client)
    key = await _issue_own_key(client, headers)

    response = await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "disabled"},
        headers=headers,
    )
    assert response.status_code == 200

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.resource_id == key["id"],
                    AuditEvent.action == "auth.key.set_state",
                )
                .order_by(AuditEvent.created_at.desc())
            )
        ).scalars().all()
        assert rows, "expected an auth.key.set_state audit row"
        latest = rows[0]
        assert latest.outcome == "success"
        assert latest.detail.get("state") == "disabled"
        assert latest.actor_subject_id is not None  # self-service, actor recorded
