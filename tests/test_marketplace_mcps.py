from __future__ import annotations

import pytest

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
