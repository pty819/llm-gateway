from __future__ import annotations

import io
import zipfile

import pytest

from sqlmodel import col, select as sqlselect

from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.db.models import (
    ResourceState,
    Subject,
    SubjectType,
    Team,
    TeamMembership,
)
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
    """A unique slug per call. Tests share a persistent DB with no per-test
    isolation, so a fixed slug left by one test could collide on a later run.
    Unique slugs avoid cross-test interference."""
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


async def test_upload_duplicate_slug_different_owner_allowed():
    alice = await _make_user()
    bob = await _make_user()
    slug = _unique_slug("weather")
    async with AsyncSessionLocal() as session:
        alice_skill = await create_or_append_skill_version(
            session, actor=alice, slug=slug, name="W", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        bob_skill = await create_or_append_skill_version(
            session, actor=bob, slug=slug, name="W", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await session.commit()
    # alice/weather and bob/weather coexist as independent skills
    assert alice_skill.id != bob_skill.id
    assert alice_skill.owner_subject_id == alice.id
    assert bob_skill.owner_subject_id == bob.id
    assert alice_skill.slug == bob_skill.slug == slug
    assert alice_skill.latest_version == bob_skill.latest_version == "1.0.0"


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


async def test_list_visible_guest_grant_equals_public():
    """A skill granted to the builtin 'guest' team is visible to every subject
    that is a guest member."""
    owner = await _make_user()
    consumer = await _make_user()

    async with AsyncSessionLocal() as session:
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        session.add(TeamMembership(team_id=guest.id, subject_id=consumer.id))
        await session.commit()

    slug = _unique_slug("pub")
    async with AsyncSessionLocal() as session:
        skill = await create_or_append_skill_version(
            session, actor=owner, slug=slug, name="Pub", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await ensure_skill_team_grant(session, skill_id=skill.id, team_id=guest.id)
        await session.commit()

    from llm_gateway.services.registry import list_visible_skills

    async with AsyncSessionLocal() as session:
        items, total = await list_visible_skills(session, subject_id=consumer.id)
        assert any(s.slug == slug for s in items), [s.slug for s in items]
        assert total >= 1


async def test_list_visible_excludes_unauthorized():
    owner = await _make_user()
    stranger = await _make_user()
    slug = _unique_slug("secret")
    async with AsyncSessionLocal() as session:
        await create_or_append_skill_version(
            session, actor=owner, slug=slug, name="Secret", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await session.commit()

    from llm_gateway.services.registry import list_visible_skills

    async with AsyncSessionLocal() as session:
        items, _ = await list_visible_skills(session, subject_id=stranger.id)
        assert all(s.slug != slug for s in items)
        items_owner, _ = await list_visible_skills(session, subject_id=owner.id)
        assert any(s.slug == slug for s in items_owner)


async def test_latest_active_version_falls_back_when_pointer_disabled():
    """If latest_version points at a row that no longer resolves as active,
    fall back to the most recent active version by created_at."""
    owner = await _make_user()
    slug = _unique_slug("fallback")
    from llm_gateway.services.registry import get_latest_active_version, get_skill_version

    async with AsyncSessionLocal() as session:
        s1 = await create_or_append_skill_version(
            session, actor=owner, slug=slug, name="F", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip("1"),
        )
        await session.commit()
        await create_or_append_skill_version(
            session, actor=owner, slug=slug, name="F", version="2.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip("2"),
        )
        await session.commit()
        # artificially disable the v2 row (the current latest)
        v2 = await get_skill_version(session, skill_id=s1.id, version="2.0.0")
        assert v2 is not None
        v2.state = ResourceState.DISABLED
        await session.commit()
        await session.refresh(s1)

    async with AsyncSessionLocal() as session:
        latest = await get_latest_active_version(session, skill=s1)
        assert latest is not None
        assert latest.version == "1.0.0"  # fell back to most recent active
