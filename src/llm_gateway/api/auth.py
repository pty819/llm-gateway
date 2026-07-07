"""Auth API — aggregates session, key, managed-resource, and marketplace
self-service routers under the /auth prefix.

This module is a thin aggregator; the route logic lives in:
- api/auth_session.py     (register/login/logout/me/password/profile)
- api/auth_keys.py        (self-service gateway keys)
- api/managed.py          (managed project/team resource management)
- api/marketplace_self.py (self-service skill/mcp registry)

main.py does ``app.include_router(auth.router)``; that call is unchanged by
this split. Sub-routers carry no prefix of their own, so their paths compose
under ``/auth`` to the exact same URLs as before (e.g. ``/auth/register``,
``/auth/managed/projects``, ``/auth/registry/skills/browse/{owner}/{slug}``).
"""

from __future__ import annotations

from fastapi import APIRouter

from llm_gateway.api.auth_keys import router as keys_router
from llm_gateway.api.auth_session import router as session_router
from llm_gateway.api.managed import router as managed_router
from llm_gateway.api.marketplace_self import router as marketplace_router

router = APIRouter(prefix="/auth")
router.include_router(session_router)
router.include_router(keys_router)
router.include_router(managed_router)
router.include_router(marketplace_router)
