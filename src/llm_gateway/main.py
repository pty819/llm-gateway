from fastapi import FastAPI

from llm_gateway.api import admin, health, proxy
from llm_gateway.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(proxy.router)
    return app


app = create_app()

