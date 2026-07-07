from contextlib import asynccontextmanager

from fastapi import FastAPI

from llm_gateway.api import api_router, mcp_server
from llm_gateway.core.config import get_settings
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.facts_queue import drain_now
from llm_gateway.services.security import ensure_builtin_identity

_DEFAULT_ADMIN_TOKEN = "dev-admin-token"
_DEFAULT_ADMIN_PASSWORD = "dev-admin-password"


def _guard_default_admin_credentials(settings) -> None:
    """Refuse to start with the shipped default admin credentials outside local
    environments — a default admin token or password is an instant takeover."""
    if not settings.should_require_nondefault_admin_credentials():
        return
    insecure = []
    if settings.admin_token == _DEFAULT_ADMIN_TOKEN:
        insecure.append("LLM_GATEWAY_ADMIN_TOKEN")
    if settings.bootstrap_admin_password == _DEFAULT_ADMIN_PASSWORD:
        insecure.append("LLM_GATEWAY_BOOTSTRAP_ADMIN_PASSWORD")
    if insecure:
        raise RuntimeError(
            "Refusing to start: default admin credentials are still set ("
            + ", ".join(insecure)
            + "). Override them before running outside a local environment."
        )


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncSessionLocal() as session:
            await ensure_builtin_identity(session, settings)
            await session.commit()
        _guard_default_admin_credentials(settings)
        # Health checking runs in a separate sidecar process (python -m
        # llm_gateway.health_sidecar), NOT here. A main-process event-loop
        # freeze must not be able to take out the whole upstream fleet via
        # false-positive probe timeouts. The sidecar has its own GIL and
        # writes runtime liveness to Redis; this process only reads it on
        # the routing path. See README "部署" for the sidecar invocation.
        # Start the MCP server's session manager task group (the SDK app is
        # mounted as a sub-app; its own lifespan doesn't run under FastAPI).
        async with mcp_server.mcp_lifespan():
            yield
        # Flush any in-flight request facts before the process exits so a
        # restart/SIGTERM never silently drops accounting data.
        await drain_now()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(api_router)
    # MCP server (Streamable HTTP) — mounted as an ASGI sub-app under /v1/mcp.
    # The SDK app's route path is set to "" so mounting at /v1/mcp is exact.
    app.mount("/v1/mcp", mcp_server.create_mcp_asgi_app())
    return app


app = create_app()
