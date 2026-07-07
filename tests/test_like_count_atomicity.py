"""Verify toggle_skill_like / toggle_mcp_like use an atomic UPDATE for like_count.

The read-modify-write pattern (skill.like_count += 1) loses updates under
concurrency. The fix is an atomic UPDATE ... SET like_count = like_count + 1,
mirroring increment_skill_download_count. True concurrency is hard to achieve
in a single-threaded asyncio test (tasks serialize on one event loop, and the
async DB driver serializes statements on one connection), so this file uses
two complementary checks:

1. Pattern verification: capture the SQL emitted by toggle_skill_like and
   assert it contains ``like_count = like_count + 1`` (the atomic form), not
   a parameter-bound value derived from a stale read.
2. Correctness: fire several likes from distinct subjects and confirm the
   final like_count equals the number of like rows (no drift).
"""

from __future__ import annotations

import io
import zipfile
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import dialect as pg_dialect

from llm_gateway.db.models import (
    MCP,
    MCPTransport,
    McpVersion,
    Skill,
    SkillLike,
    SkillVersion,
    Subject,
    SubjectType,
)
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.registry import toggle_mcp_like, toggle_skill_like

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", "content")
    return buf.getvalue()


async def _make_subject() -> Subject:
    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        s = Subject(name=f"atom-{suffix}", type=SubjectType.USER)
        session.add(s)
        await session.commit()
        await session.refresh(s)
    return s


async def _make_skill(owner: Subject) -> Skill:
    async with AsyncSessionLocal() as session:
        skill = Skill(owner_subject_id=owner.id, slug=f"atom-{uuid4().hex[:8]}", name="S")
        session.add(skill)
        await session.flush()
        session.add(
            SkillVersion(
                skill_id=skill.id,
                version="1.0.0",
                content_blob=_make_zip(),
                content_sha256="a" * 64,
                size_bytes=1,
                upload_subject_id=owner.id,
            )
        )
        await session.commit()
        await session.refresh(skill)
    return skill


async def _make_mcp(owner: Subject) -> MCP:
    async with AsyncSessionLocal() as session:
        mcp = MCP(owner_subject_id=owner.id, slug=f"atom-{uuid4().hex[:8]}", name="M")
        session.add(mcp)
        await session.flush()
        session.add(
            McpVersion(
                mcp_id=mcp.id,
                version="1.0.0",
                transport=MCPTransport.STDIO,
                args=[],
                env={},
                headers={},
                tools=[],
                upload_subject_id=owner.id,
            )
        )
        await session.commit()
        await session.refresh(mcp)
    return mcp


async def test_toggle_skill_like_emits_atomic_update() -> None:
    """toggle_skill_like must emit UPDATE skills SET like_count = like_count + 1."""
    owner = await _make_subject()
    skill = await _make_skill(owner)
    liker = await _make_subject()

    compiled: list[str] = []

    async with AsyncSessionLocal() as session:

        @event.listens_for(session.sync_session, "do_orm_execute")
        def _capture(exec_state):  # noqa: ANN001
            stmt = exec_state.statement
            try:
                compiled.append(
                    str(stmt.compile(dialect=pg_dialect(), compile_kwargs={"literal_binds": True}))
                )
            except Exception:  # noqa: BLE001
                pass

        await toggle_skill_like(session, subject_id=liker.id, skill_id=skill.id)

    # At least one emitted statement must be an atomic increment: the column
    # is referenced on both sides of the assignment, not bound to a literal.
    assert any(
        "UPDATE skills" in s and "like_count" in s and "like_count + 1" in s for s in compiled
    ), [s for s in compiled if "UPDATE" in s]


async def test_concurrent_likes_match_row_count() -> None:
    """Firing N likes from N distinct subjects yields like_count == N.

    Each toggle runs in its own session; the like_count column must end up
    equal to the number of like rows, proving no increment was lost."""
    owner = await _make_subject()
    skill = await _make_skill(owner)
    likers = [await _make_subject() for _ in range(5)]

    import asyncio

    async def _like(subject_id):
        async with AsyncSessionLocal() as session:
            await toggle_skill_like(session, subject_id=subject_id, skill_id=skill.id)
            await session.commit()

    await asyncio.gather(*[_like(s.id) for s in likers])

    async with AsyncSessionLocal() as session:
        from sqlalchemy import func, select

        refreshed = await session.get(Skill, skill.id)
        assert refreshed is not None
        n_likes = (
            await session.execute(
                select(func.count()).select_from(SkillLike).where(SkillLike.skill_id == skill.id)
            )
        ).scalar_one()
    assert refreshed.like_count == n_likes == len(likers)


async def test_concurrent_mcp_likes_match_row_count() -> None:
    """Same check for MCP likes."""
    owner = await _make_subject()
    mcp = await _make_mcp(owner)
    likers = [await _make_subject() for _ in range(5)]

    import asyncio

    async def _like(subject_id):
        async with AsyncSessionLocal() as session:
            await toggle_mcp_like(session, subject_id=subject_id, mcp_id=mcp.id)
            await session.commit()

    await asyncio.gather(*[_like(s.id) for s in likers])

    async with AsyncSessionLocal() as session:
        refreshed = await session.get(MCP, mcp.id)
        assert refreshed is not None
        from sqlalchemy import func, select

        from llm_gateway.db.models import McpLike

        n_likes = (
            await session.execute(
                select(func.count()).select_from(McpLike).where(McpLike.mcp_id == mcp.id)
            )
        ).scalar_one()
    assert refreshed.like_count == n_likes == len(likers)
