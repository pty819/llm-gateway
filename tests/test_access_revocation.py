from sqlmodel import col, select

from llm_gateway.db.models import (
    GatewayKey,
    ModelEntitlement,
    Project,
    ResourceState,
    Subject,
)
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.policy import subject_can_use_model
from llm_gateway.services.security import AuthContext


async def test_disabling_entitlement_revokes_access_immediately(gateway_fixture):
    """subject_can_use_model must reflect DB state on every call, so revoking an
    entitlement takes effect immediately rather than after a cache TTL window."""
    async with AsyncSessionLocal() as session:
        key = (
            await session.execute(
                select(GatewayKey).where(col(GatewayKey.id) == gateway_fixture.key_id)
            )
        ).scalar_one()
        subject = (
            await session.execute(
                select(Subject).where(col(Subject.id) == gateway_fixture.subject_id)
            )
        ).scalar_one()
        project = (
            await session.execute(
                select(Project).where(col(Project.id) == gateway_fixture.project_id)
            )
        ).scalar_one()
        auth = AuthContext(key=key, subject=subject, project=project)

        assert (
            await subject_can_use_model(
                session, auth=auth, model_alias_id=gateway_fixture.model_alias_id
            )
            is True
        )

        entitlement = (
            await session.execute(
                select(ModelEntitlement).where(
                    col(ModelEntitlement.project_id) == gateway_fixture.project_id,
                    col(ModelEntitlement.model_alias_id)
                    == gateway_fixture.model_alias_id,
                )
            )
        ).scalar_one()
        entitlement.state = ResourceState.DISABLED
        await session.commit()

        # No sleep, no cache invalidation call — must already be revoked.
        assert (
            await subject_can_use_model(
                session, auth=auth, model_alias_id=gateway_fixture.model_alias_id
            )
            is False
        )
