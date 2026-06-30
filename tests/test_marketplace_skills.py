from __future__ import annotations

import io
import zipfile

import pytest

from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.db.models import Subject, SubjectType, Team, TeamMembership
from llm_gateway.services.registry import (
    create_or_append_skill_version,
    ensure_skill_team_grant,
    subject_can_access_skill,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_zip(text: str = "SKILL.md content") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", text)
    return buf.getvalue()


def _unique_slug(base: str = "skill") -> str:
    """A unique active slug per call. Tests share a persistent DB with no
    per-test isolation, and active slugs must be globally unique, so a fixed
    slug left by one test would collide on a later run."""
    from uuid import uuid4

    return f"{base}-{uuid4().hex[:8]}"


async def _make_user(login_username: str | None = None) -> Subject:
    from uuid import uuid4

    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        subject = Subject(
            name=f"name-{suffix}",
            type=SubjectType.USER,
            login_username=login_username,
        )
        session.add(subject)
        await session.commit()
        await session.refresh(subject)
    return subject


async def test_resolve_owner_by_login_username_then_name():
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Subject, SubjectType
    from uuid import uuid4

    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        subject = Subject(
            name=f"display-{suffix}",
            type=SubjectType.USER,
            login_username=f"l{(uuid4().int % 100_000_000):08d}",
        )
        session.add(subject)
        await session.commit()
        await session.refresh(subject)

    from llm_gateway.services.registry import resolve_owner_subject

    async with AsyncSessionLocal() as session:
        by_username = await resolve_owner_subject(session, owner=subject.login_username)
        assert by_username is not None and by_username.id == subject.id
        by_name = await resolve_owner_subject(session, owner=subject.name)
        assert by_name is not None and by_name.id == subject.id
        missing = await resolve_owner_subject(session, owner="does-not-exist-xyz")
        assert missing is None


async def test_upload_creates_skill_and_first_version():
    owner = await _make_user()
    slug = _unique_slug("weather")
    async with AsyncSessionLocal() as session:
        skill = await create_or_append_skill_version(
            session,
            actor=owner,
            slug=slug,
            name="Weather",
            version="1.0.0",
            summary="s",
            description=None,
            notes=None,
            zip_bytes=_make_zip(),
        )
        await session.commit()
        assert skill.latest_version == "1.0.0"
        assert skill.owner_subject_id == owner.id
        from llm_gateway.db.models import SkillVersion
        import sqlmodel

        versions = (
            (await session.execute(sqlmodel.select(SkillVersion)))
            .scalars()
            .all()
        )
        mine = [v for v in versions if v.skill_id == skill.id]
        assert len(mine) == 1
        assert mine[0].size_bytes > 0
        assert len(mine[0].content_sha256) == 64


async def test_upload_append_new_version_updates_latest():
    owner = await _make_user()
    slug = _unique_slug("x")
    async with AsyncSessionLocal() as session:
        await create_or_append_skill_version(
            session, actor=owner, slug=slug, name="X", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip("v1"),
        )
        skill = await create_or_append_skill_version(
            session, actor=owner, slug=slug, name="X", version="2.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip("v2"),
        )
        await session.commit()
        assert skill.latest_version == "2.0.0"


async def test_upload_duplicate_version_raises_conflict():
    from fastapi import HTTPException

    owner = await _make_user()
    slug = _unique_slug("x")
    async with AsyncSessionLocal() as session:
        await create_or_append_skill_version(
            session, actor=owner, slug=slug, name="X", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as exc:
            await create_or_append_skill_version(
                session, actor=owner, slug=slug, name="X", version="1.0.0",
                summary=None, description=None, notes=None, zip_bytes=_make_zip(),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "version_conflict"


async def test_upload_slug_taken_by_other_owner_raises_conflict():
    from fastapi import HTTPException

    alice = await _make_user()
    bob = await _make_user()
    slug = _unique_slug("weather")
    async with AsyncSessionLocal() as session:
        await create_or_append_skill_version(
            session, actor=alice, slug=slug, name="W", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as exc:
            await create_or_append_skill_version(
                session, actor=bob, slug=slug, name="W", version="1.0.0",
                summary=None, description=None, notes=None, zip_bytes=_make_zip(),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "artifact_slug_conflict"


async def test_grant_upsert_and_visibility():
    import uuid as _uuid

    owner = await _make_user()
    consumer = await _make_user()
    slug = _unique_slug("s")
    async with AsyncSessionLocal() as session:
        skill = await create_or_append_skill_version(
            session, actor=owner, slug=slug, name="S", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        assert not await subject_can_access_skill(
            session, subject_id=consumer.id, skill=skill
        )
        team = Team(name=f"team-{_uuid.uuid4().hex}")
        session.add(team)
        await session.flush()
        session.add(TeamMembership(team_id=team.id, subject_id=consumer.id))
        await session.flush()
        await ensure_skill_team_grant(
            session, skill_id=skill.id, team_id=team.id
        )
        await session.commit()
        await session.refresh(skill)
        assert await subject_can_access_skill(
            session, subject_id=consumer.id, skill=skill
        )
