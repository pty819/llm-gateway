from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlmodel import col

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
load_dotenv(ROOT / ".env.local")


# ---------------------------------------------------------------------------
# Test database isolation
# ---------------------------------------------------------------------------
#
# Previously, conftest ran ``alembic upgrade head`` against whatever DB
# ``get_settings().database_url`` resolved to (i.e. ``.env.local``'s dev DB)
# and never isolated test data. Every test that writes via ``AsyncSessionLocal``
# polluted that shared DB, which is why several marketplace tests failed
# intermittently (stale ``dl-*`` slugs / rows from prior runs).
#
# Now the suite requires ``LLM_GATEWAY_TEST_DATABASE_URL`` (a *separate* DSN).
# conftest builds an engine against it, runs migrations there, and rebinds the
# global session factory to it via ``reconfigure_engine``. An autouse
# function-scoped fixture truncates every table before each test for full
# isolation. If the var is unset, the ENTIRE suite skips (gracefully) rather
# than silently touching the dev DB.


def _truncate_all_tables(conn) -> None:
    """TRUNCATE every user table in the public schema (sync, run via run_sync).

    ``alembic_version`` is deliberately EXCLUDED: it tracks which migrations have
    been applied. Wiping it makes the next session's ``alembic upgrade head``
    think the DB is empty and re-run every migration from scratch, failing with
    "relation already exists".
    """
    result = conn.execute(
        text(
            "SELECT string_agg(tablename, ', ') FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename != 'alembic_version'"
        )
    )
    table_list = result.scalar()
    if table_list:
        # RESTART IDENTITY resets sequences; CASCADE drops dependent rows so FK
        # ordering does not matter.
        conn.execute(text(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE"))


def _migrate_test_db_if_configured() -> str | None:
    """Run alembic migrations against the test DB at CONLECTION time (module
    import), before pytest-asyncio starts its session event loop.

    alembic/env.py internally calls ``asyncio.run()``, which fails if a loop is
    already running. By doing this at conftest module load (well before any
    fixture/loop starts), we sidestep that conflict entirely.

    Returns the test DSN if migrations ran, or None if no test DB is configured
    (in which case DB-touching tests will skip at fixture time).
    """
    from llm_gateway.core.config import get_settings

    settings = get_settings()
    test_url = settings.test_database_url
    if not test_url:
        return None

    # alembic/env.py resolves the target DSN via get_settings().database_url,
    # which is @lru_cache-cached and points at the dev DB. Override the env var
    # + clear the cache so alembic targets the test DB, then restore.
    previous_db_url = os.environ.get("LLM_GATEWAY_DATABASE_URL")
    os.environ["LLM_GATEWAY_DATABASE_URL"] = test_url
    get_settings.cache_clear()
    try:
        from scripts.init_db import main as init_db

        init_db()
    finally:
        if previous_db_url is None:
            os.environ.pop("LLM_GATEWAY_DATABASE_URL", None)
        else:
            os.environ["LLM_GATEWAY_DATABASE_URL"] = previous_db_url
        # Reset settings cache to dev values; the test engine is bound separately
        # via reconfigure_engine in the fixture below.
        get_settings.cache_clear()
    return test_url


# Run migrations once at collection time (before any event loop starts).
_TEST_DB_URL: str | None = _migrate_test_db_if_configured()


@pytest.fixture(scope="session")
def test_database_engine():
    """Rebind the global engine to the (already-migrated) test DB.

    Requires ``LLM_GATEWAY_TEST_DATABASE_URL``. If unset, skips — which
    propagates to any test that pulls this fixture via ``_isolate_tables``.
    Tests marked ``@pytest.mark.no_db`` never pull it.

    NOT autouse: requested on demand by ``_isolate_tables`` (which IS autouse)
    only for tests that aren't marked no_db. Migrations already ran at module
    load (see ``_migrate_test_db_if_configured`` above) to avoid an asyncio.run
    conflict with the pytest-asyncio session loop.
    """
    if not _TEST_DB_URL:
        pytest.skip(
            "LLM_GATEWAY_TEST_DATABASE_URL is not set; DB-touching tests require "
            "a dedicated test database to avoid polluting the dev DB. Set it in "
            ".env.local (gitignored), e.g. pointing at a separate database on the "
            "same instance. (Tests marked @pytest.mark.no_db still run.)",
            allow_module_level=False,
        )

    from sqlalchemy.ext.asyncio import create_async_engine

    from llm_gateway.db import session as db_session

    test_engine = create_async_engine(_TEST_DB_URL, pool_pre_ping=True)
    db_session.reconfigure_engine(test_engine)

    yield

    # Best-effort dispose; the session loop may be closing. Connections are
    # reclaimed by the OS on process exit regardless.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(test_engine.dispose())
        else:
            loop.run_until_complete(test_engine.dispose())
    except RuntimeError:
        pass


@pytest.fixture(autouse=True)
async def _isolate_tables(request):
    """Truncate all tables before each test for full isolation, then re-seed
    the builtin identity (guest/admin teams + bootstrap admin) that several
    tests assume exists (e.g. they query ``Team name == 'guest'`` directly
    without going through the app lifespan that normally seeds it).

    Short-circuits (no truncation, no seed) for tests marked
    ``@pytest.mark.no_db`` — those are pure unit tests that never touch the DB.
    """
    if request.node.get_closest_marker("no_db"):
        return
    # Pull test_database_engine to ensure it has run (set up the test DB /
    # skipped if unavailable). For no_db tests we returned above, so only
    # DB-touching tests reach this dependency.
    request.getfixturevalue("test_database_engine")
    from llm_gateway.core.config import get_settings
    from llm_gateway.db import session as db_session
    from llm_gateway.services.security import ensure_builtin_identity

    async with db_session.engine.begin() as conn:
        await conn.run_sync(_truncate_all_tables)

    # Re-seed builtin identity on the test DB. This mirrors what the app
    # lifespan does on startup; tests that use the ``client`` fixture get this
    # via the ASGI lifespan, but tests that touch the DB directly via
    # AsyncSessionLocal need it re-established after each truncation.
    async with db_session.AsyncSessionLocal() as session:
        await ensure_builtin_identity(session, get_settings())
        await session.commit()


@pytest_asyncio.fixture
async def client():
    from llm_gateway.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=90
    ) as item:
        yield item


@pytest_asyncio.fixture
async def external_ip_client():
    from llm_gateway.main import app

    transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 12345))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=90
    ) as item:
        yield item


@dataclass(frozen=True)
class GatewayFixture:
    raw_key: str
    model_alias: str
    subject_id: UUID
    project_id: UUID
    key_id: UUID
    model_alias_id: UUID
    upstream_id: UUID


@pytest_asyncio.fixture
async def gateway_fixture() -> GatewayFixture:
    from llm_gateway.db.models import (
        ModelAlias,
        ModelEntitlement,
        Project,
        Subject,
        SubjectType,
        UpstreamTarget,
    )
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import create_gateway_key

    suffix = uuid4().hex
    upstream_model = os.environ["LLM_GATEWAY_UPSTREAM_MODEL"]

    async with AsyncSessionLocal() as session:
        subject = Subject(name=f"pytest-user-{suffix}", type=SubjectType.USER)
        session.add(subject)
        await session.flush()

        project = Project(name=f"pytest-project-{suffix}", owner_subject_id=subject.id)
        session.add(project)
        await session.flush()

        model_alias = ModelAlias(
            alias=f"pytest-model-{suffix}",
            upstream_model_name=upstream_model,
        )
        session.add(model_alias)
        await session.flush()

        upstream = UpstreamTarget(
            model_alias_id=model_alias.id,
            name=f"pytest-upstream-{suffix}",
            base_url=os.environ["LLM_GATEWAY_UPSTREAM_BASE_URL"],
            api_key_value=os.environ["LLM_GATEWAY_UPSTREAM_API_KEY"],
        )
        session.add(upstream)
        await session.flush()

        entitlement = ModelEntitlement(project_id=project.id, model_alias_id=model_alias.id)
        session.add(entitlement)
        key, raw_key = await create_gateway_key(
            session,
            subject_id=subject.id,
            project_id=project.id,
            name=f"pytest-key-{suffix}",
        )
        await session.commit()

        return GatewayFixture(
            raw_key=raw_key,
            model_alias=model_alias.alias,
            subject_id=subject.id,
            project_id=project.id,
            key_id=key.id,
            model_alias_id=model_alias.id,
            upstream_id=upstream.id,
        )


async def fetch_request_fact(request_id: str):
    from llm_gateway.db.models import RequestFact
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.facts_queue import drain_now

    for _ in range(20):
        await drain_now()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(RequestFact).where(col(RequestFact.request_id) == request_id)
            )
            fact = result.scalar_one_or_none()
            if fact is not None:
                return fact
        await asyncio.sleep(0.05)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RequestFact).where(col(RequestFact.request_id) == request_id)
        )
        return result.scalar_one()
