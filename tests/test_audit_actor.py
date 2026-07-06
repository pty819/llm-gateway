from uuid import uuid4

from llm_gateway.db.models import Subject, SubjectType
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.facts import admin_actor_subject_id, record_audit_event


async def _make_subject(session) -> Subject:
    subject = Subject(name=f"audit-actor-{uuid4().hex[:8]}", type=SubjectType.USER)
    session.add(subject)
    await session.flush()
    return subject


async def test_audit_event_picks_up_context_actor_by_default():
    async with AsyncSessionLocal() as session:
        actor = await _make_subject(session)
        token = admin_actor_subject_id.set(actor.id)
        try:
            event = await record_audit_event(
                session,
                action="test.context_actor",
                resource_type="test",
                outcome="success",
            )
            await session.commit()
        finally:
            admin_actor_subject_id.reset(token)
    assert event.actor_subject_id == actor.id


async def test_explicit_actor_overrides_context_actor():
    async with AsyncSessionLocal() as session:
        context_actor = await _make_subject(session)
        explicit_actor = await _make_subject(session)
        token = admin_actor_subject_id.set(context_actor.id)
        try:
            event = await record_audit_event(
                session,
                action="test.explicit_actor",
                resource_type="test",
                outcome="success",
                actor_subject_id=explicit_actor.id,
            )
            await session.commit()
        finally:
            admin_actor_subject_id.reset(token)
    assert event.actor_subject_id == explicit_actor.id


async def test_audit_event_without_context_actor_is_null():
    token = admin_actor_subject_id.set(None)
    try:
        async with AsyncSessionLocal() as session:
            event = await record_audit_event(
                session,
                action="test.null_actor",
                resource_type="test",
                outcome="success",
            )
            await session.commit()
        assert event.actor_subject_id is None
    finally:
        admin_actor_subject_id.reset(token)
