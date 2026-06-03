from __future__ import annotations

import pytest

from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.cache import auth_cache, route_cache
from llm_gateway.services.policy import resolve_route_context
from llm_gateway.services.security import authenticate_gateway_key


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_auth_cache_hit_reloads_objects_in_current_session(gateway_fixture):
    auth_cache.invalidate()

    async with AsyncSessionLocal() as session:
        first = await authenticate_gateway_key(session, gateway_fixture.raw_key)
        assert first is not None
        first_key = first.key

    async with AsyncSessionLocal() as session:
        second = await authenticate_gateway_key(session, gateway_fixture.raw_key)
        assert second is not None
        assert second.key.id == gateway_fixture.key_id
        assert second.key is not first_key
        assert second.key in session


async def test_route_cache_hit_reloads_objects_in_current_session(gateway_fixture):
    auth_cache.invalidate()
    route_cache.invalidate()

    async with AsyncSessionLocal() as session:
        auth = await authenticate_gateway_key(session, gateway_fixture.raw_key)
        assert auth is not None
        first = await resolve_route_context(
            session,
            auth=auth,
            requested_model=gateway_fixture.model_alias,
            client_ip="127.0.0.1",
        )
        first_alias = first.model_alias

    async with AsyncSessionLocal() as session:
        auth = await authenticate_gateway_key(session, gateway_fixture.raw_key)
        assert auth is not None
        second = await resolve_route_context(
            session,
            auth=auth,
            requested_model=gateway_fixture.model_alias,
            client_ip="127.0.0.1",
        )
        assert second.model_alias.id == gateway_fixture.model_alias_id
        assert second.model_alias is not first_alias
        assert second.model_alias in session
