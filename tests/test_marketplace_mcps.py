from __future__ import annotations

import pytest

from tests.test_marketplace_skills import _login_user_with_key
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.db.models import Subject, Team
from llm_gateway.services.registry import (
    create_or_append_mcp_version,
    ensure_mcp_team_grant,
)
from sqlmodel import select as sqlselect
from sqlmodel import col
from tests.test_backend_integration import _auth_headers

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_user(login_username: str | None = None):
    from uuid import uuid4
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Subject, SubjectType

    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        subject = Subject(
            name=f"mcp-name-{suffix}",
            type=SubjectType.USER,
            login_username=login_username,
        )
        session.add(subject)
        await session.commit()
        await session.refresh(subject)
    return subject


def _unique_slug(base: str) -> str:
    """tests share a persistent DB; unique slugs avoid cross-test interference."""
    from uuid import uuid4
    return f"{base}-{uuid4().hex[:8]}"


def _mcp_config(*, transport="stdio", command="uvx mcp-server-x", url=None,
                args=None, env=None, headers=None, tools=None):
    return {
        "transport": transport,
        "command": command,
        "url": url,
        "args": args if args is not None else [],
        "env": env if env is not None else {"API_KEY": "secret-value"},
        "headers": headers if headers is not None else {},
        "tools": tools if tools is not None else [],
    }


async def test_create_mcp_creates_artifact_and_first_version():
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import McpVersion
    from llm_gateway.services.registry import create_or_append_mcp_version
    import sqlmodel

    owner = await _make_user()
    slug = _unique_slug("weather-mcp")
    async with AsyncSessionLocal() as session:
        mcp = await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="Weather MCP", version="1.0.0",
            summary="s", description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
        assert mcp.latest_version == "1.0.0"
        assert mcp.owner_subject_id == owner.id
        versions = (
            await session.execute(sqlmodel.select(McpVersion))
        ).scalars().all()
        mine = [v for v in versions if v.mcp_id == mcp.id]
        assert len(mine) == 1
        assert mine[0].transport.value == "stdio"
        assert mine[0].env == {"API_KEY": "secret-value"}


async def test_append_mcp_version_updates_latest():
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.registry import create_or_append_mcp_version

    owner = await _make_user()
    slug = _unique_slug("append")
    async with AsyncSessionLocal() as session:
        await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="M", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        mcp = await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="M", version="2.0.0",
            summary=None, description=None, notes=None,
            config=_mcp_config(command="uvx mcp-server-x@2"),
        )
        await session.commit()
        assert mcp.latest_version == "2.0.0"


async def test_mcp_duplicate_version_raises_conflict():
    from fastapi import HTTPException
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.registry import create_or_append_mcp_version

    owner = await _make_user()
    slug = _unique_slug("dup")
    async with AsyncSessionLocal() as session:
        await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="D", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as exc:
            await create_or_append_mcp_version(
                session, actor=owner, slug=slug, name="D", version="1.0.0",
                summary=None, description=None, notes=None, config=_mcp_config(),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "version_conflict"


async def test_mcp_cross_owner_slug_coexists():
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.registry import create_or_append_mcp_version

    alice = await _make_user()
    bob = await _make_user()
    slug = _unique_slug("shared")
    async with AsyncSessionLocal() as session:
        a = await create_or_append_mcp_version(
            session, actor=alice, slug=slug, name="A", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        b = await create_or_append_mcp_version(
            session, actor=bob, slug=slug, name="B", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
    assert a.id != b.id
    assert a.owner_subject_id == alice.id and b.owner_subject_id == bob.id
    assert a.slug == b.slug == slug


async def test_mcp_grant_upsert_and_visibility():
    import uuid as _uuid
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Team, TeamMembership
    from llm_gateway.services.registry import (
        create_or_append_mcp_version,
        ensure_mcp_team_grant,
        subject_can_access_mcp,
    )

    owner = await _make_user()
    consumer = await _make_user()
    slug = _unique_slug("grant")
    async with AsyncSessionLocal() as session:
        mcp = await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="G", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        assert not await subject_can_access_mcp(session, subject_id=consumer.id, mcp=mcp)
        team = Team(name=f"mcp-team-{_uuid.uuid4().hex}")
        session.add(team)
        await session.flush()
        session.add(TeamMembership(team_id=team.id, subject_id=consumer.id))
        await session.flush()
        await ensure_mcp_team_grant(session, mcp_id=mcp.id, team_id=team.id)
        await session.commit()
        await session.refresh(mcp)
        assert await subject_can_access_mcp(session, subject_id=consumer.id, mcp=mcp)


async def _publish_and_grant_to_guest(owner_id, slug):
    """Publish an MCP owned by owner_id and grant to the builtin guest team.
    Returns (mcp_id, slug)."""
    async with AsyncSessionLocal() as session:
        owner = await session.get(Subject, owner_id)
        mcp = await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="Weather MCP", version="1.0.0",
            summary="weather mcp", description=None, notes=None, config=_mcp_config(),
        )
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        await ensure_mcp_team_grant(session, mcp_id=mcp.id, team_id=guest.id)
        await session.commit()
        return mcp.id, slug


async def test_dataplane_mcp_list_detail_and_redaction(client):
    _, _, username, owner_id = await _login_user_with_key(client)
    slug = _unique_slug("weather-mcp")
    await _publish_and_grant_to_guest(owner_id, slug)

    # a DIFFERENT registered user (also a guest member) can list + read detail
    _, other_gw, _, _ = await _login_user_with_key(client)
    resp = await client.get(
        f"/v1/registry/mcps?q={slug}", headers=_auth_headers(other_gw)
    )
    assert resp.status_code == 200, resp.text
    slugs = [m["slug"] for m in resp.json()["items"]]
    assert slug in slugs, slugs

    detail = await client.get(
        f"/v1/registry/mcps/{username}/{slug}", headers=_auth_headers(other_gw)
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["slug"] == slug
    # NON-owner (other_gw) sees REDACTED env/headers
    assert body["latest"]["env"] == {"API_KEY": "***"}, body["latest"]["env"]
    for v in body["versions"]:
        assert v["env"] == {"API_KEY": "***"}
    # transport + command + tools (non-secret) are visible
    assert body["latest"]["transport"] == "stdio"
    assert body["latest"]["command"] == "uvx mcp-server-x"


async def test_dataplane_owner_sees_cleartext(client):
    """The OWNER querying their own mcp via the data plane sees cleartext env."""
    _, owner_gw, username, owner_id = await _login_user_with_key(client)
    slug = _unique_slug("own")
    await _publish_and_grant_to_guest(owner_id, slug)
    detail = await client.get(
        f"/v1/registry/mcps/{username}/{slug}", headers=_auth_headers(owner_gw)
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["latest"]["env"] == {"API_KEY": "secret-value"}


async def test_dataplane_hidden_mcp_returns_404(client):
    _, _, _, alice_id = await _login_user_with_key(client)
    async with AsyncSessionLocal() as session:
        alice = await session.get(Subject, alice_id)
        private_slug = _unique_slug("private")
        await create_or_append_mcp_version(
            session, actor=alice, slug=private_slug, name="P", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
    # need alice's username for the path; re-login to get it
    _, _, alice_login, _ = await _login_user_with_key(client)
    _, other_gw, _, _ = await _login_user_with_key(client)
    resp = await client.get(
        f"/v1/registry/mcps/{alice_login}/nope-mcp", headers=_auth_headers(other_gw)
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "artifact_not_found"


async def test_dataplane_mcp_no_gateway_key_401(client):
    resp = await client.get("/v1/registry/mcps")
    assert resp.status_code == 401
