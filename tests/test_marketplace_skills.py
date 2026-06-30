from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


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
