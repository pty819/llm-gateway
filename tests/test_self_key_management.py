from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _login_self_service_user(client):
    """Create a fresh user with a personal project + key + session.

    Bypasses /auth/register to avoid the per-IP login/register rate limit when
    this module runs as part of the full suite (many tests register from the
    same 127.0.0.1). The key-management endpoints under test only need a valid
    session + a key on the user's personal project, which we build directly.
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
        subject, project, _key, _raw = await create_registered_user(
            session,
            username=username,
            full_name="自助用户",
            password="correct-horse-battery",
        )
        user_session, raw_session = await create_user_session(
            session,
            subject_id=subject.id,
            ttl_hours=get_settings().session_ttl_hours,
        )
        await session.commit()
        headers = {"x-session-token": raw_session}
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
            (
                await session.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.resource_id == key["id"],
                        AuditEvent.action == "auth.key.set_state",
                    )
                    .order_by(AuditEvent.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        assert rows, "expected an auth.key.set_state audit row"
        latest = rows[0]
        assert latest.outcome == "success"
        assert latest.detail.get("state") == "disabled"
        assert latest.actor_subject_id is not None  # self-service, actor recorded


async def test_disable_key_on_non_personal_project_returns_404(client):
    """Spec row 4: a key admin issued to this user on a NON-personal project must
    not be self-disableable. Covers the project_id permission clause, which is
    the security-critical branch (subject_id matches but project_id does not)."""
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.models import Project
    from llm_gateway.db.session import AsyncSessionLocal

    headers, username = await _login_self_service_user(client)
    # Resolve the user's subject_id and create a separate (non-personal) project.
    from sqlalchemy import select
    from sqlmodel import col

    from llm_gateway.db.models import Subject

    async with AsyncSessionLocal() as session:
        subject = (
            await session.execute(select(Subject).where(col(Subject.login_username) == username))
        ).scalar_one()
        subject_id = subject.id
        other_project = Project(name=f"other-team-project-{uuid4().hex}", owner_subject_id=None)
        session.add(other_project)
        await session.commit()
        other_project_id = other_project.id

    # Admin issues a key for this user on the non-personal project.
    admin_headers = {"x-admin-token": get_settings().admin_token}
    issue = await client.post(
        "/admin/gateway-keys",
        json={
            "subject_id": str(subject_id),
            "project_id": str(other_project_id),
            "name": "team-key",
        },
        headers=admin_headers,
    )
    assert issue.status_code == 200, issue.text
    team_key = issue.json()["key"]

    # User tries to self-disable it -> 404 (not their personal-project key).
    response = await client.patch(
        f"/auth/keys/{team_key['id']}/state",
        json={"state": "disabled"},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "key_not_found"


async def test_disable_key_with_invalid_state_returns_422(client):
    """Spec row 6: an invalid state value is rejected by pydantic (422)."""
    headers, _ = await _login_self_service_user(client)
    key = await _issue_own_key(client, headers)

    response = await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "frozen"},
        headers=headers,
    )
    assert response.status_code == 422
