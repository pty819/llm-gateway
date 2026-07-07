"""HTTP API routers.

Aggregates all top-level routers into a single ``api_router`` so
``main.py`` only needs one ``app.include_router(api_router)`` call.
The ``api/admin`` sub-package follows the same self-aggregation pattern
internally (see ``api/admin/__init__.py``).

The MCP server is mounted separately in ``main.py`` as an ASGI sub-app
under ``/v1/mcp`` (it is not a FastAPI router).
"""

from fastapi import APIRouter

from llm_gateway.api import auth, health, proxy, realtime, registry
from llm_gateway.api.admin import router as admin_router

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin_router)
api_router.include_router(realtime.router)
api_router.include_router(proxy.router)
api_router.include_router(registry.router)

__all__ = ["api_router"]
