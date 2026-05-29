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
    return create_async_engine(resolved.database_url, pool_pre_ping=True)


engine = create_engine()
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

_analytics_factory: async_sessionmaker[AsyncSession] | None = None


def analytics_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    global _analytics_factory
    resolved = settings or get_settings()
    if not resolved.analytics_database_url:
        return AsyncSessionLocal
    if _analytics_factory is not None:
        return _analytics_factory
    eng = create_async_engine(
        resolved.analytics_database_url,
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )
    _analytics_factory = async_sessionmaker(
        eng, class_=AsyncSession, expire_on_commit=False
    )
    return _analytics_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_analytics_session() -> AsyncGenerator[AsyncSession, None]:
    async with analytics_session_factory()() as session:
        yield session
