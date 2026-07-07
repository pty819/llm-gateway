import sys
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from llm_gateway.core.config import Settings, get_settings


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    resolved = settings or get_settings()
    # Explicit pool sizing: the defaults (5 + 10 overflow) are too small for a
    # FastAPI gateway where the proxy data plane, admin control plane, and the
    # async facts queue all draw from the same engine. pre_ping guards against
    # the database dropping idle connections; recycle bounds a connection's age.
    return create_async_engine(
        resolved.database_url,
        pool_pre_ping=True,
        pool_size=resolved.db_pool_size,
        max_overflow=resolved.db_max_overflow,
        pool_recycle=resolved.db_pool_recycle_seconds,
    )


engine: AsyncEngine = create_engine()
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def reconfigure_engine(new_engine: AsyncEngine) -> None:
    """Replace the global ``engine`` and ``AsyncSessionLocal`` in place.

    Used ONLY by tests to point the module-level session factory at a
    dedicated test database (and truncate it between tests), so the dev DB in
    ``.env.local`` is never touched. Production code never calls this.

    Why rebind rather than mutate: ``async_sessionmaker`` has no clean in-place
    reconfigure API, so we rebuild it. But a plain rebind of this module's global
    is insufficient on its own: pytest imports (collects) every test module
    BEFORE session fixtures run, and some test modules do
    ``from llm_gateway.db.session import AsyncSessionLocal`` at module top level
    — capturing the *old* factory in their own namespace. Such a module would
    silently keep pointing at the dev engine. To make the swap robust we capture
    the OLD factory first, then walk ``sys.modules`` and update every module
    holding that exact reference. Modules that import lazily (inside
    functions/fixtures) pick up the new value automatically via normal
    module-attribute lookup.

    Note: we deliberately do NOT await ``dispose()`` on the old engine here —
    that would force this function to be async (and thus impossible to call from
    a sync session fixture). The old engine is simply dropped; Python's GC will
    close its connections. For tests this is fine: the dev engine's pool is
    pristine (never used) at the point we swap it out.
    """
    global engine, AsyncSessionLocal
    old_factory = AsyncSessionLocal
    engine = new_engine
    AsyncSessionLocal = async_sessionmaker(new_engine, class_=AsyncSession, expire_on_commit=False)

    # Propagate the rebound factory to every module that captured the old one.
    this_module = sys.modules[__name__]
    for mod in sys.modules.values():
        if mod is None or mod is this_module:
            continue
        if getattr(mod, "AsyncSessionLocal", None) is old_factory:
            mod.AsyncSessionLocal = AsyncSessionLocal


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
