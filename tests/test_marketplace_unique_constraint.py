"""Verify the UNIQUE(owner_subject_id, slug) constraint on skills and mcps.

Migration 0011 created these constraints at the DB level
(``uq_skill_owner_slug`` / ``uq_mcp_owner_slug``), but the ORM models only
declared ``__table_args__`` for the *like* tables — Skill/MCP omitted it, so
the model drifted from the schema. Task 3.1 re-declares the constraint on the
models; this test proves the DB-level guarantee works by inserting a duplicate
(owner, slug) row directly (bypassing create_or_append_*_version, which has its
own read-then-write that would also reject the duplicate at the app layer).
"""

from __future__ import annotations

import io
import zipfile
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select as sqlselect

from llm_gateway.db.models import MCP, Skill, Subject, SubjectType
from llm_gateway.db.session import AsyncSessionLocal

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_subject() -> Subject:
    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        subject = Subject(name=f"uniq-{suffix}", type=SubjectType.USER)
        session.add(subject)
        await session.commit()
        await session.refresh(subject)
    return subject


async def test_duplicate_skill_owner_slug_rejected_by_db() -> None:
    """Two Skill rows with the same (owner_subject_id, slug) must conflict."""
    owner = await _make_subject()
    slug = f"dup-skill-{uuid4().hex[:8]}"

    async with AsyncSessionLocal() as session:
        first = Skill(owner_subject_id=owner.id, slug=slug, name="A")
        session.add(first)
        await session.commit()

    async with AsyncSessionLocal() as session:
        dup = Skill(owner_subject_id=owner.id, slug=slug, name="B")
        session.add(dup)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_duplicate_mcp_owner_slug_rejected_by_db() -> None:
    """Two MCP rows with the same (owner_subject_id, slug) must conflict."""
    owner = await _make_subject()
    slug = f"dup-mcp-{uuid4().hex[:8]}"

    async with AsyncSessionLocal() as session:
        first = MCP(owner_subject_id=owner.id, slug=slug, name="A")
        session.add(first)
        await session.commit()

    async with AsyncSessionLocal() as session:
        dup = MCP(owner_subject_id=owner.id, slug=slug, name="B")
        session.add(dup)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_different_owners_same_slug_allowed() -> None:
    """Different owners may share a slug (alice/x and bob/x coexist)."""
    a = await _make_subject()
    b = await _make_subject()
    slug = f"shared-{uuid4().hex[:8]}"

    async with AsyncSessionLocal() as session:
        session.add(Skill(owner_subject_id=a.id, slug=slug, name="A"))
        session.add(Skill(owner_subject_id=b.id, slug=slug, name="B"))
        await session.commit()

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sqlselect(Skill).where(Skill.slug == slug))).scalars().all()
        assert {r.owner_subject_id for r in rows} == {a.id, b.id}


def _make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", "content")
    return buf.getvalue()
