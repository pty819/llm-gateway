from contextlib import asynccontextmanager

from fastapi import FastAPI

from llm_gateway.api import admin, auth, health, proxy, realtime
from llm_gateway.core.config import get_settings
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.duckdb_analytics import close_analytics, init_analytics
from llm_gateway.services.security import ensure_builtin_identity


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncSessionLocal() as session:
            await ensure_builtin_identity(session, settings)
            await session.commit()
        init_analytics(settings)
        yield
        close_analytics()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(realtime.router)
    app.include_router(proxy.router)
    return app


app = create_app()
