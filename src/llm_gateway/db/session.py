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
    #
    # idle_in_transaction_session_timeout is the wedge guard: a request that is
    # aborted mid-transaction (client disconnect while a query is in flight)
    # can leave its session idle-in-transaction holding row locks. Every
    # request that then touches those rows blocks, each blocker pins a pool
    # connection, and the whole engine wedges. Postgres now reaps such
    # transactions itself, so the worst case is one failed request instead of
    # a dead control plane.
    return create_async_engine(
        resolved.database_url,
        pool_pre_ping=True,
        pool_size=resolved.db_pool_size,
        max_overflow=resolved.db_max_overflow,
        pool_recycle=resolved.db_pool_recycle_seconds,
        connect_args={
            "server_settings": {
                "idle_in_transaction_session_timeout": "30000",
            },
            "timeout": 10,
        },
    )


engine = create_engine()
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
