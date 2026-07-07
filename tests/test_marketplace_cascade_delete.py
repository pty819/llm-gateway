"""Verify ON DELETE cascades added by migration 0015 on marketplace tables.

Migration 0009 added ondelete to core tables but predated the marketplace
tables. 0015 adds CASCADE to owner/grant/like FKs and SET NULL to the
upload_subject_id FK on version tables. These tests exercise the DB-level
cascades directly (deleting a subject / skill / mcp and asserting the children
are gone or preserved per policy).
"""

from __future__ import annotations

import io
import zipfile
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from llm_gateway.db.models import (
    MCP,
    McpLike,
    McpTeamGrant,
    MCPTransport,
    McpVersion,
    Skill,
    SkillLike,
    SkillTeamGrant,
    SkillVersion,
    Subject,
    SubjectType,
    Team,
    TeamMembership,
)
from llm_gateway.db.session import AsyncSessionLocal

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", "content")
    return buf.getvalue()


async def _make_subject() -> Subject:
    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        s = Subject(name=f"casc-{suffix}", type=SubjectType.USER)
        session.add(s)
        await session.commit()
        await session.refresh(s)
    return s


async def _make_skill(owner: Subject) -> Skill:
    async with AsyncSessionLocal() as session:
        skill = Skill(owner_subject_id=owner.id, slug=f"sk-{uuid4().hex[:8]}", name="S")
        session.add(skill)
        await session.flush()
        version = SkillVersion(
            skill_id=skill.id,
            version="1.0.0",
            content_blob=_make_zip(),
            content_sha256="a" * 64,
            size_bytes=1,
            upload_subject_id=owner.id,
        )
        session.add(version)
        await session.commit()
        await session.refresh(skill)
        await session.refresh(version)
    return skill


async def _make_mcp(owner: Subject) -> MCP:
    async with AsyncSessionLocal() as session:
        mcp = MCP(owner_subject_id=owner.id, slug=f"mc-{uuid4().hex[:8]}", name="M")
        session.add(mcp)
        await session.flush()
        version = McpVersion(
            mcp_id=mcp.id,
            version="1.0.0",
            transport=MCPTransport.STDIO,
            args=[],
            env={},
            headers={},
            tools=[],
            upload_subject_id=owner.id,
        )
        session.add(version)
        await session.commit()
        await session.refresh(mcp)
    return mcp


async def _count(table, **filters) -> int:
    async with AsyncSessionLocal() as session:
        stmt = select(table)
        for k, v in filters.items():
            stmt = stmt.where(getattr(table, k) == v)
        return len((await session.execute(stmt)).scalars().all())


async def test_delete_subject_cascades_skill_chain() -> None:
    """Deleting a subject cascades to their skill, its version, grants, likes."""
    owner = await _make_subject()
    skill = await _make_skill(owner)

    liker = await _make_subject()
    team = Team(name=f"team-{uuid4().hex}")
    async with AsyncSessionLocal() as session:
        session.add(team)
        await session.flush()
        session.add(TeamMembership(team_id=team.id, subject_id=liker.id))
        session.add(SkillTeamGrant(skill_id=skill.id, team_id=team.id))
        session.add(SkillLike(subject_id=liker.id, skill_id=skill.id))
        await session.commit()

    # sanity: children exist
    assert await _count(Skill, owner_subject_id=owner.id) == 1
    assert await _count(SkillVersion, skill_id=skill.id) == 1
    assert await _count(SkillTeamGrant, skill_id=skill.id) == 1
    assert await _count(SkillLike, skill_id=skill.id) == 1

    # delete the owner subject -> cascade through the whole chain
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Subject).where(Subject.id == owner.id))
        await session.commit()

    assert await _count(Skill, owner_subject_id=owner.id) == 0
    assert await _count(SkillVersion, skill_id=skill.id) == 0
    assert await _count(SkillTeamGrant, skill_id=skill.id) == 0
    # the liker still exists; the like row was removed by the skill cascade
    assert await _count(SkillLike, skill_id=skill.id) == 0


async def test_delete_skill_cascades_versions_grants_likes() -> None:
    """Deleting a skill directly removes its versions, grants, and likes."""
    owner = await _make_subject()
    skill = await _make_skill(owner)
    liker = await _make_subject()
    team = Team(name=f"team-{uuid4().hex}")
    async with AsyncSessionLocal() as session:
        session.add(team)
        await session.flush()
        session.add(TeamMembership(team_id=team.id, subject_id=liker.id))
        session.add(SkillTeamGrant(skill_id=skill.id, team_id=team.id))
        session.add(SkillLike(subject_id=liker.id, skill_id=skill.id))
        await session.commit()

    async with AsyncSessionLocal() as session:
        await session.execute(delete(Skill).where(Skill.id == skill.id))
        await session.commit()

    assert await _count(SkillVersion, skill_id=skill.id) == 0
    assert await _count(SkillTeamGrant, skill_id=skill.id) == 0
    assert await _count(SkillLike, skill_id=skill.id) == 0


async def test_delete_uploader_cascades_version_when_skill_survives() -> None:
    """Deleting the uploader removes version rows they uploaded, even when the
    parent skill (owned by someone else) survives. upload_subject_id is NOT
    NULL, so the FK is CASCADE (not SET NULL); the version row is deleted."""
    skill_owner = await _make_subject()
    uploader = await _make_subject()
    async with AsyncSessionLocal() as session:
        sk = Skill(owner_subject_id=skill_owner.id, slug=f"hist-{uuid4().hex[:8]}", name="H")
        session.add(sk)
        await session.flush()
        session.add(
            SkillVersion(
                skill_id=sk.id,
                version="1.0.0",
                content_blob=_make_zip(),
                content_sha256="b" * 64,
                size_bytes=1,
                upload_subject_id=uploader.id,
            )
        )
        await session.commit()
        skill_id = sk.id

    async with AsyncSessionLocal() as session:
        await session.execute(delete(Subject).where(Subject.id == uploader.id))
        await session.commit()

    # skill survives (different owner), but the version row is gone
    assert await _count(Skill, id=skill_id) == 1
    assert await _count(SkillVersion, skill_id=skill_id) == 0


async def test_delete_subject_cascades_mcp_chain() -> None:
    """Deleting a subject cascades to their mcp, its version, grants, likes."""
    owner = await _make_subject()
    mcp = await _make_mcp(owner)
    liker = await _make_subject()
    team = Team(name=f"team-{uuid4().hex}")
    async with AsyncSessionLocal() as session:
        session.add(team)
        await session.flush()
        session.add(TeamMembership(team_id=team.id, subject_id=liker.id))
        session.add(McpTeamGrant(mcp_id=mcp.id, team_id=team.id))
        session.add(McpLike(subject_id=liker.id, mcp_id=mcp.id))
        await session.commit()

    async with AsyncSessionLocal() as session:
        await session.execute(delete(Subject).where(Subject.id == owner.id))
        await session.commit()

    assert await _count(MCP, owner_subject_id=owner.id) == 0
    assert await _count(McpVersion, mcp_id=mcp.id) == 0
    assert await _count(McpTeamGrant, mcp_id=mcp.id) == 0
    assert await _count(McpLike, mcp_id=mcp.id) == 0


async def test_delete_team_cascades_skill_grant() -> None:
    """Deleting a team removes grants that referenced it."""
    owner = await _make_subject()
    skill = await _make_skill(owner)
    team = Team(name=f"team-{uuid4().hex}")
    async with AsyncSessionLocal() as session:
        session.add(team)
        await session.flush()
        session.add(SkillTeamGrant(skill_id=skill.id, team_id=team.id))
        await session.commit()
        team_id = team.id

    async with AsyncSessionLocal() as session:
        await session.execute(delete(Team).where(Team.id == team_id))
        await session.commit()

    assert await _count(SkillTeamGrant, skill_id=skill.id) == 0
