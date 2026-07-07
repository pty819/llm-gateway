from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlmodel import col
from sqlmodel import select as sqlselect

from llm_gateway.db.models import AuditEvent, Subject, Team
from llm_gateway.db.session import AsyncSessionLocal

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _unique_slug(base: str = "audit") -> str:
    """Tests share a persistent DB with no per-test isolation; a unique slug
    avoids collisions across runs."""
    return f"{base}-{uuid4().hex[:8]}"


async def _login_self_service_user(client):
    """Create a fresh user with a session token. Mirrors the pattern in
    test_marketplace_skills._login_user_with_key but returns only what the
    audit tests need (headers + subject id)."""
    from llm_gateway.core.config import get_settings
    from llm_gateway.services.security import (
        create_gateway_key,
        create_registered_user,
        create_user_session,
    )
    from tests.helpers import _employee_username

    username = _employee_username()
    async with AsyncSessionLocal() as session:
        subject, project, _key, _raw = await create_registered_user(
            session,
            username=username,
            full_name="审计用户",
            password="correct-horse-battery",
        )
        user_session, raw_session = await create_user_session(
            session,
            subject_id=subject.id,
            ttl_hours=get_settings().session_ttl_hours,
        )
        await create_gateway_key(
            session,
            subject_id=subject.id,
            project_id=project.id,
            name="audit-key",
        )
        await session.commit()
    return {"x-session-token": raw_session}, subject.id


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


async def _latest_audit_event(action: str) -> AuditEvent | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AuditEvent)
            .where(col(AuditEvent.action) == action)
            .order_by(col(AuditEvent.created_at).desc(), col(AuditEvent.id).desc())
        )
        return result.scalars().first()


# ---- Task 1.2: admin marketplace state-change audit ----


async def test_admin_patch_skill_state_audited(client):
    from tests.test_marketplace_skills import _make_zip

    sess_headers, *_ = await _login_self_service_user(client)
    slug = _unique_slug("auditskill")
    up = await client.post(
        "/auth/registry/skills",
        headers=sess_headers,
        data={"slug": slug, "name": "Audit", "version": "1.0.0"},
        files={"file": ("a.zip", _make_zip(), "application/zip")},
    )
    assert up.status_code == 200, up.text
    skill_id = up.json()["skill"]["id"]

    admin = await _admin_headers(client)
    r = await client.patch(
        f"/admin/registry/skills/{skill_id}/state",
        headers=admin,
        json={"state": "disabled"},
    )
    assert r.status_code == 200, r.text

    event = await _latest_audit_event("skill.set_state")
    assert event is not None, "no audit event recorded for skill.set_state"
    assert event.resource_type == "skill"
    assert str(event.resource_id) == skill_id
    assert event.detail.get("state") == "disabled"


async def test_admin_patch_mcp_state_audited(client):
    sess_headers, *_ = await _login_self_service_user(client)
    slug = _unique_slug("auditmcp")
    config = {
        "transport": "stdio",
        "command": "uvx mcp-server-x",
        "url": None,
        "args": [],
        "env": {},
        "headers": {},
        "tools": [],
    }
    up = await client.post(
        "/auth/registry/mcps",
        headers=sess_headers,
        json={"slug": slug, "name": "AuditMcp", "version": "1.0.0", "config": config},
    )
    assert up.status_code == 200, up.text
    mcp_id = up.json()["mcp"]["id"]

    admin = await _admin_headers(client)
    r = await client.patch(
        f"/admin/registry/mcps/{mcp_id}/state",
        headers=admin,
        json={"state": "disabled"},
    )
    assert r.status_code == 200, r.text

    event = await _latest_audit_event("mcp.set_state")
    assert event is not None, "no audit event recorded for mcp.set_state"
    assert event.resource_type == "mcp"
    assert str(event.resource_id) == mcp_id
    assert event.detail.get("state") == "disabled"


# ---- Task 1.3: self-service grant/like write audit ----


async def _guest_team_id() -> str:
    async with AsyncSessionLocal() as session:
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        return str(guest.id)


async def test_self_skill_grant_create_audited(client):
    from tests.test_marketplace_skills import _make_zip

    sess_headers, subject_id = await _login_self_service_user(client)
    slug = _unique_slug("grantaudit")
    up = await client.post(
        "/auth/registry/skills",
        headers=sess_headers,
        data={"slug": slug, "name": "GA", "version": "1.0.0"},
        files={"file": ("g.zip", _make_zip(), "application/zip")},
    )
    assert up.status_code == 200, up.text
    skill_id = up.json()["skill"]["id"]
    guest_id = await _guest_team_id()

    r = await client.post(
        f"/auth/registry/skills/me/{slug}/grants",
        headers=sess_headers,
        json={"team_id": guest_id},
    )
    assert r.status_code == 200, r.text
    grant_id = r.json()["grant"]["id"]

    event = await _latest_audit_event("self.skill_grant.create")
    assert event is not None, "no audit event recorded for self.skill_grant.create"
    assert event.resource_type == "skill_team_grant"
    assert str(event.resource_id) == grant_id
    # Self-service handlers MUST record the actor explicitly (contextvar is unset).
    assert event.actor_subject_id is not None
    assert str(event.actor_subject_id) == str(subject_id)
    assert event.detail.get("skill_id") == skill_id
    assert event.detail.get("team_id") == guest_id


async def test_self_skill_grant_set_state_audited(client):
    from tests.test_marketplace_skills import _make_zip

    sess_headers, *_ = await _login_self_service_user(client)
    slug = _unique_slug("stateaudit")
    await client.post(
        "/auth/registry/skills",
        headers=sess_headers,
        data={"slug": slug, "name": "SA", "version": "1.0.0"},
        files={"file": ("s.zip", _make_zip(), "application/zip")},
    )
    guest_id = await _guest_team_id()
    create = await client.post(
        f"/auth/registry/skills/me/{slug}/grants",
        headers=sess_headers,
        json={"team_id": guest_id},
    )
    grant_id = create.json()["grant"]["id"]

    r = await client.patch(
        f"/auth/registry/skills/me/{slug}/grants/{grant_id}/state",
        headers=sess_headers,
        json={"state": "disabled"},
    )
    assert r.status_code == 200, r.text

    event = await _latest_audit_event("self.skill_grant.set_state")
    assert event is not None, "no audit event recorded for self.skill_grant.set_state"
    assert event.resource_type == "skill_team_grant"
    assert str(event.resource_id) == grant_id
    assert event.actor_subject_id is not None
    assert event.detail.get("state") == "disabled"


async def test_self_mcp_grant_create_audited(client):
    sess_headers, subject_id = await _login_self_service_user(client)
    slug = _unique_slug("mcpgrant")
    config = {
        "transport": "stdio",
        "command": "uvx mcp-server-x",
        "url": None,
        "args": [],
        "env": {},
        "headers": {},
        "tools": [],
    }
    up = await client.post(
        "/auth/registry/mcps",
        headers=sess_headers,
        json={"slug": slug, "name": "MG", "version": "1.0.0", "config": config},
    )
    assert up.status_code == 200, up.text
    mcp_id = up.json()["mcp"]["id"]
    guest_id = await _guest_team_id()

    r = await client.post(
        f"/auth/registry/mcps/me/{slug}/grants",
        headers=sess_headers,
        json={"team_id": guest_id},
    )
    assert r.status_code == 200, r.text
    grant_id = r.json()["grant"]["id"]

    event = await _latest_audit_event("self.mcp_grant.create")
    assert event is not None, "no audit event recorded for self.mcp_grant.create"
    assert event.resource_type == "mcp_team_grant"
    assert str(event.resource_id) == grant_id
    assert event.actor_subject_id is not None
    assert str(event.actor_subject_id) == str(subject_id)
    assert event.detail.get("mcp_id") == mcp_id
    assert event.detail.get("team_id") == guest_id


async def test_self_mcp_grant_set_state_audited(client):
    sess_headers, *_ = await _login_self_service_user(client)
    slug = _unique_slug("mcpstate")
    config = {
        "transport": "stdio",
        "command": "uvx mcp-server-x",
        "url": None,
        "args": [],
        "env": {},
        "headers": {},
        "tools": [],
    }
    await client.post(
        "/auth/registry/mcps",
        headers=sess_headers,
        json={"slug": slug, "name": "MS", "version": "1.0.0", "config": config},
    )
    guest_id = await _guest_team_id()
    create = await client.post(
        f"/auth/registry/mcps/me/{slug}/grants",
        headers=sess_headers,
        json={"team_id": guest_id},
    )
    grant_id = create.json()["grant"]["id"]

    r = await client.patch(
        f"/auth/registry/mcps/me/{slug}/grants/{grant_id}/state",
        headers=sess_headers,
        json={"state": "disabled"},
    )
    assert r.status_code == 200, r.text

    event = await _latest_audit_event("self.mcp_grant.set_state")
    assert event is not None, "no audit event recorded for self.mcp_grant.set_state"
    assert event.resource_type == "mcp_team_grant"
    assert str(event.resource_id) == grant_id
    assert event.actor_subject_id is not None
    assert event.detail.get("state") == "disabled"


async def test_self_skill_like_unlike_audited(client):
    from tests.test_marketplace_skills import _make_zip

    sess_headers, subject_id = await _login_self_service_user(client)
    slug = _unique_slug("likeaudit")
    guest_id = await _guest_team_id()
    up = await client.post(
        "/auth/registry/skills",
        headers=sess_headers,
        data={"slug": slug, "name": "Like", "version": "1.0.0"},
        files={"file": ("l.zip", _make_zip(), "application/zip")},
    )
    skill_id = up.json()["skill"]["id"]
    # grant to guest so the owner can "browse" their own skill and like it
    await client.post(
        f"/auth/registry/skills/me/{slug}/grants",
        headers=sess_headers,
        json={"team_id": guest_id},
    )
    owner_name = await _fetch_subject_name(subject_id)

    like = await client.post(
        f"/auth/registry/skills/browse/{owner_name}/{slug}/like",
        headers=sess_headers,
    )
    assert like.status_code == 200, like.text
    event = await _latest_audit_event("self.skill.like")
    assert event is not None, "no audit event recorded for self.skill.like"
    assert event.resource_type == "skill"
    assert str(event.resource_id) == skill_id
    assert event.actor_subject_id is not None

    unlike = await client.delete(
        f"/auth/registry/skills/browse/{owner_name}/{slug}/like",
        headers=sess_headers,
    )
    assert unlike.status_code == 200, unlike.text
    event = await _latest_audit_event("self.skill.unlike")
    assert event is not None, "no audit event recorded for self.skill.unlike"
    assert str(event.resource_id) == skill_id


async def test_self_mcp_like_unlike_audited(client):
    sess_headers, subject_id = await _login_self_service_user(client)
    slug = _unique_slug("mcplike")
    guest_id = await _guest_team_id()
    config = {
        "transport": "stdio",
        "command": "uvx mcp-server-x",
        "url": None,
        "args": [],
        "env": {},
        "headers": {},
        "tools": [],
    }
    up = await client.post(
        "/auth/registry/mcps",
        headers=sess_headers,
        json={"slug": slug, "name": "LikeMcp", "version": "1.0.0", "config": config},
    )
    assert up.status_code == 200, up.text
    mcp_id = up.json()["mcp"]["id"]
    # grant to guest so the owner can "browse" their own mcp and like it
    await client.post(
        f"/auth/registry/mcps/me/{slug}/grants",
        headers=sess_headers,
        json={"team_id": guest_id},
    )
    owner_name = await _fetch_subject_name(subject_id)

    like = await client.post(
        f"/auth/registry/mcps/browse/{owner_name}/{slug}/like",
        headers=sess_headers,
    )
    assert like.status_code == 200, like.text
    event = await _latest_audit_event("self.mcp.like")
    assert event is not None, "no audit event recorded for self.mcp.like"
    assert event.resource_type == "mcp"
    assert str(event.resource_id) == mcp_id
    assert event.actor_subject_id is not None

    unlike = await client.delete(
        f"/auth/registry/mcps/browse/{owner_name}/{slug}/like",
        headers=sess_headers,
    )
    assert unlike.status_code == 200, unlike.text
    event = await _latest_audit_event("self.mcp.unlike")
    assert event is not None, "no audit event recorded for self.mcp.unlike"
    assert event.resource_type == "mcp"
    assert str(event.resource_id) == mcp_id


async def _fetch_subject_name(subject_id) -> str:
    async with AsyncSessionLocal() as session:
        subject = await session.get(Subject, subject_id)
        return subject.login_username or subject.name
